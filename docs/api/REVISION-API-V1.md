═══════════════════════════════════════════════════════════════
API REVIEW — Yachay v1 (socket Unix)
═══════════════════════════════════════════════════════════════

Resource:        Motor de datos Yachay — API v1
Base Path:       /v1 (sobre AF_UNIX, sin listener TCP)
Style:           REST (RPC-ligero; envelope propio, no RFC 7807)
Version:         v1 (en el envelope: `api_version`)
Auth:            ninguna — control de acceso = modo del socket (0660) + grupo
Ámbito:          src/motor/api/{envelope,routes,server}.py (1043 LOC)
Evidencia:       lectura estática + 9 sondas contra un servidor real
───────────────────────────────────────────────────────────────

RESUMEN

La API es sólida y disciplinada: superficie cerrada, envelope único con códigos
cerrados, transacciones explícitas, escritura atómica con `O_EXCL`, defensa
contra traversal verificada, y proyección sin filtrar el interior (sin rutas ni
leases). La revisión encontró **1 defecto alto de observabilidad**, 3 medios y
3 bajos. No hay hallazgos críticos ni de seguridad explotable.

───────────────────────────────────────────────────────────────
HALLAZGOS
───────────────────────────────────────────────────────────────

| ID     | Sev.  | Tipo   | Ubicación                       | Resumen                                              | Esf. | Certeza    |
|--------|-------|--------|---------------------------------|------------------------------------------------------|------|------------|
| API-01 | Alto  | Bug    | routes.py:732-738 + server.py:108 | Todo error tras el routing loguea `route: <unrouted>` | S    | VERIFICADO |
| API-02 | Medio | Diseño | routes.py:622-627                | Id malformado devuelve 404, no 400 (indistinguible)   | S    | VERIFICADO |
| API-03 | Medio | Bug    | routes.py:285-308               | `POST /v1/inputs` acepta un cuerpo de 0 bytes (201)   | S    | VERIFICADO |
| API-04 | Medio | Deuda  | docs/adr/ADR-016.md:60          | Documentación de rutas desfasada                      | S    | RESUELTO |
| API-05 | Bajo  | Deuda  | routes.py:105-107                | `_one` no distingue parámetro ausente de vacío        | S    | VERIFICADO |
| API-06 | Bajo  | Mejora | routes.py:699-725                | `//v1/jobs` se resuelve como `/v1/jobs` (path sin normalizar) | S | VERIFICADO |
| API-07 | Bajo  | Doc    | routes.py (4×)                   | Mensajes de error mezclan inglés y español            | S    | VERIFICADO |

───────────────────────────────────────────────────────────────
DETALLE (Alto y Medios)
───────────────────────────────────────────────────────────────

── API-01 — La ruta se pierde en todo error (Alto, Bug) ──

Ubicación: routes.py:732-738, server.py:98-117.
Evidencia:

    $ POST /v1/inputs?name=y.csv  (Content-Type: application/json)
    {"component":"api","method":"POST","route":"<unrouted>","status":400,...}
    $ GET /v1/models/abc
    {"component":"api","method":"GET","route":"<unrouted>","status":404,...}

Causa: `Response` (el único portador de `route`) se construye solo en el camino
feliz. Cualquier `ApiError` —de `_reject_json_body`, de `_limit`, o de un
handler— sube hasta `server._handle`, donde `route` sigue siendo `"<unrouted>"`.
Resultado: **el log pierde la plantilla de ruta en el 100 % de las respuestas de
error**, que son justo las que un operador necesita agregar por endpoint.

Refutación intentada: ¿lo cubre algún test? `test_api.py` verifica el envelope y
los códigos, pero ninguno inspecciona el campo `route` del log de error. No hay
cobertura del defecto.

Impacto: se rompe la promesa del ADR-016 §15 («`route` es la plantilla»): no se
puede responder «¿qué endpoint falla más?». Sin seguridad comprometida.

Corrección: construir la ruta antes de ejecutar y adjuntarla al `ApiError` (o
devolverla en una excepción propia), de modo que `_handle` loguee la plantilla
real también en error. Es un cambio pequeño y localizado.

── API-02 — 404 para una entrada malformada (Medio, Diseño) ──

Ubicación: routes.py:622-627 (`_int_param`), usado por `_runs_list`,
`_run_create`, `_model_detail`.
Evidencia:

    GET /v1/models/abc     -> 404 not_found  "model not found"
    GET /v1/models/999999  -> 404 not_found  "model not found"
    GET /v1/diff?before=x&after=1 -> 404 not_found "la ejecución no existe"

`abc` no es un recurso ausente: es una petición malformada. El cliente recibe el
mismo `code` y el mismo `message` que para un id válido inexistente, así que no
puede decidir si corregir la entrada o dejar de buscarla. `_limit` sí lo hace
bien (400 para no numérico): la inconsistencia está dentro del mismo archivo.

Refutación intentada: ¿es intencional para no filtrar existencia? No: el
`message` es idéntico en español para ambos casos, y no hay nota que lo declare.

Impacto: contratos de cliente más frágiles; errores de integración difíciles de
diagnosticar.

Corrección: devolver `bad_request` cuando `int()` falla y `not_found` cuando el
id es numérico pero no existe.

── API-03 — Se admite un archivo vacío (Medio, Bug) ──

Ubicación: routes.py:285-308.
Evidencia:

    $ POST /v1/inputs?name=x.csv   (cuerpo 0 bytes)
    -> 201 {"accepted": true, "name": "x.csv", "size_bytes": 0}

Un cuerpo de cero bytes se deposita en `inbox/` y devuelve 201. El escáner lo
admitirá y creará un job que fallará más tarde en el pipeline, gastando un
intento y ensuciando la cola con un fallo evitable en la frontera.

Refutación intentada: ¿lo rechaza el escáner? El escáner admite por nombre y
tamaño de disco; el rechazar aquí es más barato y da un error accionable al
cliente en el momento, no un `failed` diferido.

Impacto: trabajo inútil del worker y ruido de estados; el cliente no sabe que su
subida era inválida hasta mucho después.

Corrección: si `request.body.size == 0`, `bad_request` («el archivo está vacío»)
antes de escribir. No rompe el contrato: sigue siendo un 400 del conjunto
cerrado.

── API-04 — El ADR quedó desincronizado (Medio, Deuda, RESUELTO) ──

Ubicación: docs/adr/ADR-016.md:60.
Evidencia histórica: el ADR declaraba «nueve rutas» y listaba 9. El código actual
expone **20 operaciones** (`_STATIC_ROUTES` 9 + `_DYNAMIC_ROUTES` 11), incluidas
las operaciones de reglas de modelado. ADR-016, ADR-019, el modelo de arquitectura,
OpenAPI y el seguimiento local deben usar esta cifra actual.

Impacto corregido: las fuentes de contrato ya no subestiman la superficie real.

Corrección: actualizar la sección 5 con las 20 operaciones (o referenciar
`docs/api/openapi.yaml`, que ya es la fuente sincronizada).

── API-05 — Ausente y vacío se confunden (Bajo, Deuda) ──

routes.py:105-107: `_one` devuelve `values[0] if values else None`. Para
`?state=` (presente y vacío) devuelve `""`, que luego cae en `state not in
JOB_STATES` → 400. El caso funciona, pero por accidente: cualquier clave futura
que trate `""` como ausente heredará un comportamiento sutil.

── API-06 — Path sin normalizar (Bajo, Mejora) ──

`//v1/jobs` resuelve a `/v1/jobs` (200) porque `http.server` colapsa la barra
en el target. Inocuo hoy, pero un contrato que prometa no hacerlo es más
predecible.

── API-07 — Idioma de los mensajes mezclado (Bajo, Doc) ──

Los mensajes de error alternan: inglés en `health`, `jobs`, `cancel`, `retry`,
`inputs` (`"job not found"`, `"the name must be a plain file name"`) y español
en modelado (`"se requiere `name`"`). El cliente no debe parsear `message`, pero
un operador que lee logs sí. Un solo idioma (español, como el resto del
producto) es más coherente.

───────────────────────────────────────────────────────────────
LO QUE ESTÁ BIEN (verificado, para no «arreglar» lo que funciona)
───────────────────────────────────────────────────────────────

- Traversal: `?name=%2e%2e%2fetc%2fpasswd` se decodifica a `../etc/passwd` y
  `_SAFE_NAME` lo rechaza. Verificado con sonda directa.
- `limit`: 0, -1, 501 y "abc" → 400. Correcto.
- Cuerpo JSON: rechazado con 400 en cualquier capitalización y con `;charset`.
- `HEAD` resuelve como `GET` sin cuerpo; método no declarado → 405 (no 404).
- Sin listener TCP; socket con modo 0660; se elimina al cerrar y se reemplaza si
  es huérfano.
- Escritura atómica `.part` + `O_EXCL` + `fsync` + `os.replace`; segunda subida
  del mismo nombre → 409, sin entrelazar bytes.
- Transacciones `BEGIN IMMEDIATE` con rollback ante cualquier fallo; el guard de
  estado de `retry` se repite en el `UPDATE` (protege contra el worker).
- Proyección sin interior: no salen `lease_until`, `worker_id`, `path` ni
  `manifest_path`.

───────────────────────────────────────────────────────────────
ESTADO DE HALLAZGOS
───────────────────────────────────────────────────────────────

| ID     | Sev.  | Estado      | Corrección / motivo                                                                 |
|--------|-------|-------------|-------------------------------------------------------------------------------------|
| API-01 | Alto  | CORREGIDO   | `dispatch` marca el `ApiError` con la ruta resuelta; `_handle` la loguea. 2 pruebas. |
| API-02 | Medio | CORREGIDO   | `_int_param` y `_diff`: id no numérico → 400; id numérico inexistente → 404.         |
| API-03 | Medio | CORREGIDO   | `POST /v1/inputs` rechaza un cuerpo de 0 bytes con 400. 1 prueba.                    |
| API-04 | Medio | CORREGIDO   | ADR-016 §5 queda alineado con las 20 operaciones actuales y apunta a `docs/api/openapi.yaml` como fuente. |
| API-05 | Bajo  | CORREGIDO   | `parse_qs(keep_blank_values=True)`: `?state=` deja de ignorarse (era 200, ahora 400). |
| API-06 | Bajo  | NO PROCEDE  | El colapso de `//` ocurre en `http.server.parse_request`, no en `routes`; la tabla es estricta. No se toca stdlib. |
| API-07 | Bajo  | CORREGIDO   | Los 25 mensajes de error unificados a español (el `code` sigue siendo el contrato).   |

**Nota sobre API-05:** en la revisión inicial se clasificó como «fragilidad latente».
La verificación con el servidor real mostró que era un **defecto vivo**: `parse_qs`
descarta valores vacíos por defecto, así que `?state=` (un desplegable sin
seleccionar) devolvía 200 con lista completa en vez de 400. Corregido.

**Método de verificación:** baseline y cierre con `./scripts/test.sh`
(416 → 420 pruebas, cobertura 100 %, ruff limpio). Cada hallazgo se reprodujo
contra un servidor real sobre socket Unix; el de API-01 se confirmó trazando
`_resolve` y el log emitido por `_handle`.

**Depurador:** no fue utilizable. `debugpy` no está instalado (entorno offline,
sin dependencias nuevas por ADR-016) y no existe `.vscode/`. La inspección en
vivo se sustituyó por ejecución del flujo real con traza del valor de `route`
en los puntos de decisión — evidencia runtime, no razonamiento.

───────────────────────────────────────────────────────────────
PENDIENTE
───────────────────────────────────────────────────────────────
Falta tu descripción del endpoint a implementar (elegiste «Otro»). Con el
método, la ruta y el propósito, lo implemento siguiendo el patrón de
`routes.py` (handler puro + entrada en la tabla + pruebas + entrada en el
OpenAPI).
═══════════════════════════════════════════════════════════════
