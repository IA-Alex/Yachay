# Bug Hunter + Codex Security — Yachay

- **Nivel:** investigación estática + runtime (reproducción con evidencia)
- **Modo:** investigar **y corregir** (opción A: encadenado en orden, autorizado)
- **Fecha:** 2026-11-12
- **Alcance:** `src/motor/` (motor de modelado, API v1, ingesta, almacenamiento)

## Comandos ejecutados

| Comando | Resultado |
| --- | --- |
| `./scripts/test.sh` | **457 pruebas OK, cobertura 100 %, ruff limpio** (tras las correcciones) |
| `.venv/bin/python` (`# reproducción`) | Reproduce y verifica BH-001/BH-004 |
| `bandit -r src` | 11 hallazgos B608 (SQLi) → **triados: falsos positivos** |
| `semgrep --config=auto src` | 15 hallazgos → triados (falsos positivos + 2 riesgos aceptados) |
| `pip-audit --path .venv/lib/python3.12/site-packages` | 5 avisos, todos en `chromadb 1.5.9` |
| `codex-security scan . -c <deepinfra.yaml>` | **Falla**: el proveedor rechaza las tools MCP del plugin |

> **Sobre el escaneo de seguridad.** Codex Security no es viable con DeepInfra
> (detalle en BH-003). Para no dejar el frente abierto, se ejecutó un escaneo
> estático real con `semgrep`, `bandit` y `pip-audit`, con **triaje manual de
> cada hallazgo**.

---

## Hallazgos

| ID | Sev. | Tipo | Ubicación | Resumen | Certeza |
|---|---|---|---|---|---|
| BH-001 | **Crítico** | Bug funcional | `src/motor/model/engine.py:98,102` | Varias reglas de proyección sobre la **misma columna**: solo se aplicaba la **última**; las demás se descartaban en silencio. Podía destruir datos (`[None, None]`) o devolver 0 filas. | VERIFICADO · **CORREGIDO** |
| BH-002 | Alto | Cobertura | `tests/unit/test_rules.py:108` | La única prueba multi-regla usaba columnas **distintas**, así que cubría al 100 % sin ejercitar el caso afectado. El gate no podía detectar BH-001. | VERIFICADO · **CORREGIDO** |
| BH-004 | Medio | Bug latente | `src/motor/model/rules.py:120-133` | `expression_for` no manejaba `domain`: devolvía la columna sin cambios (no-op silencioso). Se destapó al encadenar; afectaba también a la ruta antigua. | VERIFICADO · **CORREGIDO** |
| BH-003 | — | Informe de seguridad | Codex Security + estático | Codex Security no es viable con DeepInfra (el plugin MCP registra tools que el proveedor rechaza). Sustituido por `semgrep` + `bandit` + `pip-audit`: **0 vulnerabilidades explotables** en el código propio; 2 riesgos aceptados. | VERIFICADO |

---

## BH-001 — Reglas por columna: la última gana y el resto se descarta (Crítico)

### Síntoma

Si un usuario define **dos reglas de proyección sobre la misma columna**
(`cast`, `map` o `domain`), el resultado no refleja el modelo. Según el orden:

- `map` + `cast` → `[None, None]` (los valores se **anulan**).
- `cast` + `map` → texto crudo sin convertir (el `cast` no ocurre).
- `domain` + `map` → el `domain` desaparece por completo.
- `cast` + `filter` → **0 filas**, sin error.

### Reproducción (evidencia)

```text
$ .venv/bin/python
>>> from motor.model import engine
>>> from motor.model.rules import Rule
>>> from motor.sources.tables import Table
>>> t = Table(columns=["monto"], rows=[["1,5"], ["2,5"]])
>>> plan = [Rule(name="map", type="map", column="monto", params={"values": {",": "."}}),
...         Rule(name="cast", type="cast", column="monto", params={"to": "number"})]
>>> engine.run(t, plan).rows
[[None], [None]]                      # esperado: números 1.5, 2.5
```

Con la sonda sobre el SQL generado se ve la causa directa:

```text
$ # domain(estado) seguido de map(estado)
SQL generado: SELECT CASE WHEN "estado" = 'Pendiente' THEN 'Falta' ELSE "estado" END AS "estado" FROM t
```

El `SELECT` contiene **solo** el `CASE` del `map`. La regla `domain` no aparece:
fue descartada antes de llegar a DuckDB.

### Mecanismo (causa raíz)

`engine._build_select` colapsa las reglas de proyección en un diccionario
indexado por columna:

```python
# src/motor/model/engine.py:98
by_column = {r.column: r for r in plan if r.type in ("cast", "map", "domain")}

# src/motor/model/engine.py:102
rule = by_column.get(source)          # <- una sola regla por columna
```

Un `dict` conserva **un** valor por clave, así que al recorrer el plan la
segunda regla de la misma columna **sobrescribe** a la primera. Las reglas
`rename` (diccionario `renames`, línea 97) y `filter` (lista `filters`, línea
109) sí se acumulan; solo `cast`/`map`/`domain` sufren el colapso.

### Comportamiento esperado

La arquitectura y el contrato de la API prometen que **todas** las reglas se
aplican en su orden:

- `docs/modelado/ARQUITECTURA-MODELADO.md:64-69` define la regla como
  `(nombre, tipo, destino, parámetros)` — **sin** restricción de «una por
  columna».
- `docs/api/openapi.yaml:449` documenta la lista «en su **orden de aplicación**».
- `POST /v1/models/{id}/rules` solo impone unicidad sobre el **nombre** de la
  regla (`routes.py:671`), nunca sobre la columna.

Es decir: nada en el sistema impide crear dos reglas sobre una columna, y todo
el contrato afirma que ambas deben aplicarse. La expectativa del usuario es
aplicarlas **en secuencia** (p. ej. `map` normaliza → `cast` convierte).

### Corrección aplicada (opción A: encadenado en orden)

`engine._build_select` ya **no** colapsa las reglas de proyección en un
diccionario. Ahora agrupa las reglas `cast`/`map`/`domain` **por columna y en el
orden del plan**, y genera **una subconsulta por etapa**; cada etapa lee el alias
que dejó la anterior. La proyección final toma el último alias y lo renombra a su
destino (`rename`) o conserva el nombre. `filter` sigue aplicándose en el `WHERE`
sobre el nombre **final** de la columna (ya transformada y renombrada).

SQL generado para `domain` + `map` sobre `estado` (antes se perdía el `domain`):

```sql
SELECT "__step_1_estado" AS "estado"
FROM (SELECT CASE WHEN "__step_0_estado" = 'Pendiente' THEN 'Falta'
                  ELSE "__step_0_estado" END AS "__step_1_estado"
      FROM (SELECT CASE WHEN "estado" IN ('Hecho', 'Pendiente') THEN "estado"
                        ELSE NULL END AS "__step_0_estado"
            FROM "t") AS s0) AS s1
```

Verificación (los tres casos que antes fallaban):

```text
map+cast    -> [[1.5], [2.5], [None]]   (antes: [None, None])
domain+map  -> [['Hecho'], ['Falta'], [None]]   (antes: el domain desaparecía)
cast+filter -> [[1.5]]                  (antes: 0 filas)
```

Camino end-to-end por el API (`rules.add` → `run.execute` → `run.record`)
verificado: `[[1.5], [2.5], [None]]`, 3 filas registradas.

### BH-004 — `expression_for` no manejaba `domain` (Medio, corregido)

Al encadenar se destapó un segundo defecto: `rules.expression_for` no tenía rama
para `domain` y caía en un `return _q(column)` (identidad). Es decir, un `domain`
era un **no-op silencioso** en la proyección. En la ruta antigua quedaba oculto
(el `by_column` ya descartaba el `domain`), pero el resultado era incorrecto
igual: la regla declarada no se aplicaba.

Corregido con una rama explícita que produce la expresión restrictiva, y con un
`raise UnknownRuleType` para cualquier tipo sin expresión de proyección (en vez
de devolver la columna tal cual): un tipo futuro sin rama debe **fallar**, no
convertirse en otro no-op silencioso.

### Impacto (antes de la corrección)

- **Corrupción de datos silenciosa:** el resultado se ejecuta, se registra la
  huella y se publica como si fuera correcto. No hay error ni aviso.
- La huella (`fingerprint`) sella un resultado que **no** corresponde al modelo
  guardado, y `diff` lo reportaría como «igual» frente a otra ejecución con el
  mismo defecto.
- Rompe la promesa central del producto («defines un modelo una vez y lo
  reejecutas»): el modelo declara reglas que el motor no ejecuta.

### Corrección propuesta (no aplicada)

> **Aplicada** — ver «Corrección aplicada (opción A)» más arriba. Se conserva el
> texto original del análisis como registro del razonamiento.

Encadenar las reglas de proyección **en orden** en lugar de colapsarlas. La
demostración de que el encadenado produce el resultado correcto:

```text
map->cast encadenado: [(1.5,), (2.5,), (None,)]
cast->filter encadenado: [(1.5,)]
```

Enfoques válidos (elegir uno según el estilo del proyecto):

1. **Subconsultas anidadas:** una etapa de `SELECT` por regla, cada una leyendo
   la columna que dejó la anterior. Es el más cercano al contrato «en orden» y
   el que la prueba anterior valida.
2. **Validación explícita:** si el diseño realmente exige una sola regla de
   proyección por columna, **rechazarla** en `rules.add` y en la API con un
   error claro (409/400) — nunca descartarla en silencio. Mínimo cambio, pero
   convierte una promesa del contrato en una restricción visible.

La opción 1 preserva el contrato actual; la 2 lo estrecha. **Ninguna** debe
dejar el comportamiento actual (descartar reglas sin avisar).

---

## BH-002 — El gate no puede detectar BH-001 (Alto)

`tests/unit/test_rules.py:108` (`test_applies_rename_cast_map_and_returns_values`)
es la única prueba con varias reglas, pero las aplica a **columnas distintas**:

```python
rules.Rule(name="r2", type="map",    column="ciudad", ...)   # <- columna A
rules.Rule(name="r3", type="cast",   column="monto",  ...)   # <- columna B
rules.Rule(name="r4", type="filter", column="ciudad", ...)
rules.Rule(name="r1", type="rename", column="ciudad", ...)
```

Ningún test definía dos reglas de proyección (`cast`/`map`/`domain`) sobre la
**misma** columna. Por eso el repositorio sostenía **100 % de cobertura**
(`fail_under=100`) mientras el defecto vivía en `engine.py`. La cobertura de
líneas mide que el `dict comprehension` se ejecutó, no que su semántica
last-wins fuera correcta.

**Recomendación aplicada:** se añadieron pruebas de regresión que fijan los
valores (no solo que corre):

- `test_two_projection_rules_on_the_same_column_apply_in_order` (`map`+`cast`).
- `test_domain_before_map_on_the_same_column_keeps_both`.
- `test_cast_before_filter_on_the_same_column_filters_converted_value`.
- `test_expression_for_domain_column_restricts_values` y
  `test_expression_for_a_type_without_projection_raises` (BH-004).

### Verificación

```text
$ ./scripts/test.sh
Ran 457 tests in 11.535s
OK
TOTAL    2197      0    490      0   100%
```

(452 → 457: las 5 pruebas de regresión nuevas; ruff limpio; cobertura 100 %.)

---

## BH-003 — Escaneo de seguridad (Codex Security + análisis estático real)

### Intento con Codex Security: no viable con DeepInfra

Codex Security **no puede completar un escaneo** contra el proveedor DeepInfra.
Se resolvió la autenticación (con `DEEPINFRA_API_KEY` y un perfil de proveedor
`model_providers.deepinfra` con `wire_api: responses`), y el escaneo superó el
preflight (102 archivos inventariados, prompt ensamblado), pero **falla de forma
determinista** al emitir la llamada al modelo:

```text
{"error":{"message":"namespaced function names must be unique across all tools:
 ['_create_file','_create_folder','_delete_file','_fetch','_get_document',
  '_get_profile','_list_comments','_search','_update_file']",
 "type":"invalid_request_error"}}
```

Son las herramientas de artefacto del **plugin MCP de Codex Security**, que
DeepInfra normaliza con namespacing y rechaza por colisión. Reproducido igual con
`deepseek-ai/DeepSeek-V4-Flash` y `…-0731`. `wire_api: chat` está descartado:
Codex ya no lo admite (`wire_api = "chat" is no longer supported`).

Verificado por pruebas directas contra `/v1/openai/responses`: DeepInfra **sí**
acepta function tools en general (200), con lo que el fallo se acota al conjunto
concreto de tools namespaced del plugin. **Es una incompatibilidad
plugin↔proveedor, no del código del repositorio.**

Config usada (fuera del repo, como exige la skill):

```yaml
# /tmp/user/1000/opencode/codex-security-deepinfra.yaml
codex:
  model: deepseek-ai/DeepSeek-V4-Flash
  model_provider: deepinfra
  model_reasoning_effort: xhigh
  model_providers:
    deepinfra:
      name: DeepInfra
      base_url: https://api.deepinfra.com/v1/openai
      env_key: DEEPINFRA_API_KEY
      wire_api: responses
```

### Sustituto: análisis estático real (semgrep + bandit + pip-audit)

Al no ser viable Codex Security, se ejecutó un escaneo de seguridad con
herramientas locales estándar. **Cada hallazgo fue triado con evidencia**, no
aceptado a ciegas.

**Comandos:**

```bash
bandit -r src -q                 # SAST Python
semgrep --config=auto src        # SAST multi-regla
pip-audit --path .venv/lib/python3.12/site-packages   # dependencias del proyecto
```

#### Triaje

| Regla | Herramienta | Ubicación | Veredicto |
| --- | --- | --- | --- |
| B608 / sqlalchemy-execute-raw-query | bandit, semgrep | `engine.py` (63, 74, 131, 143, 212, 243, 246, 277), `routes.py` (211, 232, 298) | **FALSO POSITIVO** — identificadores citados con `""` y valores parametrizados |
| formatted-sql-query | semgrep | `engine.py` (63, 74, 212, 249, 277) | **FALSO POSITIVO** (mismo patrón) |
| insecure-file-permissions | semgrep | `server.py:207` (`SOCKET_MODE = 0o660`) | **RIESGO ACEPTADO (bajo)** |
| python37-compatibility-importlib2 | semgrep | `config.py:16` | **NO APLICA** — el proyecto exige Python ≥3.11 |
| PYSEC-2026-* | pip-audit | `chromadb 1.5.9` (5 avisos) | **RIESGO ACEPTADO** — transitiva de crewai, sin corrección |

#### Refutación de los SQL injection (falso positivo)

El código cita **siempre** los identificadores (`_ident`/`_q`: `""` duplica
comillas dobles) y parametriza los valores; los literales de `filter` se escapan
con `''`. Probado con ataques reales:

```text
nombre de columna: 'x" ); DROP TABLE t; --'
  -> engine.run: columnas intactas ['x" ); DROP TABLE t; --'], tabla viva
filter con valor 'a''b': devuelve [["a'b"]] sin romper la consulta
_q('a"b')    -> "a""b"
_lit("a'b")  -> 'a''b'
```

Los reportes se disparan por la construcción de la cadena SQL, pero el valor
hostil nunca llega sin citar. **No es una vulnerabilidad explotable.**

#### Socket mode 0660 (`server.py:207`)

`SOCKET_MODE = 0o660` da lectura/escritura al **grupo**. Severidad real baja:

- El socket vive en `$XDG_RUNTIME_DIR/motor/` (directorio privado del usuario,
  `0700`), así que un tercero no puede alcanzar el socket aunque el modo lo
  permitiera.
- El usuario `panda` es el único miembro de su grupo (`getent group panda` →
  `panda:x:1000:`, sin miembros adicionales).

Es intencional (permite que worker y API del mismo usuario/UID se comuniquen) y
está documentado en `docs/systemd/README.md:30` (`srw-rw----`). **Riesgo
aceptado**, no un defecto.

#### Dependencias (`chromadb 1.5.9`)

`pip-audit` sobre el venv del proyecto reporta **5 avisos, todos en
`chromadb 1.5.9`**, transitiva dura de `crewai==1.15.39` (no la importa
`src/motor`). Coincide con lo ya documentado en `docs/seguimiento-local.md`. Sin
corrección publicada: **riesgo aceptado**.

Nota: un `pip-audit` sin `--path` audita el Python **del sistema** y produce
ruido irrelevante (`requests`, `urllib3`, `ufw`, `fail2ban`…). Hay que apuntarlo
al venv del proyecto.

### Sobre el alcance

El escaneo de Codex Security **no se completó**. El análisis estático sustituto
cubre SAST del código propio y dependencias del proyecto, con triaje manual de
cada hallazgo. No incluye fuzzing, pruebas dinámicas de explotación ni revisión
del ecosistema externo.

---

## Resumen ejecutivo

1. **Bug crítico funcional (BH-001) — CORREGIDO.** El motor aplicaba solo la
   última regla de transformación por columna y descartaba las demás en
   silencio. Ahora se **encadenan en orden** (opción A), preservando el contrato
   de «varias reglas por columna aplicadas en su orden».
2. **BH-002 — CORREGIDO.** Se añadieron 5 pruebas de regresión que fijan los
   valores. Gate: **457 tests OK, 100 % de cobertura, ruff limpio**.
3. **BH-004 — CORREGIDO.** `expression_for` no manejaba `domain` (no-op
   silencioso); ahora produce la expresión restrictiva y un tipo sin proyección
   **falla** en vez de pasar la columna.
4. **Escaneo de seguridad (BH-003).** Codex Security no es viable con DeepInfra
   (el plugin MCP registra tools que el proveedor rechaza por namespacing). Se
   sustituyó por **semgrep + bandit + pip-audit**: sin vulnerabilidades
   explotables en el código propio (los reportes SQLi son falsos positivos,
   refutados); solo 2 riesgos aceptados (socket `0660` y `chromadb`).

## Preguntas abiertas

1. ~~¿Varias reglas por columna o una sola?~~ **Resuelto:** el contrato admite
   varias y se aplican en orden (opción A implementada).
2. **Opcional:** reintentar Codex Security con un proveedor que soporte su
   registro de tools MCP (OpenAI, OpenRouter), o reportar el namespacing al
   plugin. No bloquea: el escaneo estático sustituto ya cubre el código y las
   dependencias.

