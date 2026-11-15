# Criterios de cierre — Yachay (documentacion_motor_datos_local)

- **Fecha:** 2026-11-15
- **Versión declarada:** `0.1.0` (sin tag Git).
- **Base de esta revisión:** HEAD `ea0962a` (`fix: repair desktop dataset and CSV worker flows`).
  La evaluación final corresponde a ese HEAD más los cambios locales de esta
  revisión; estos cambios aún no están commiteados.
- **Evidencia histórica:** los resultados de 470 pruebas y 100 % citados antes
  pertenecen a una ejecución previa, no a `ea0962a`.
- **Auditor:** sesión bug-hunter + criterios-de-cierre (evidencia recolectada en
  el entorno real)

---

## Dictamen

**CIERRE CONDICIONADO**

Las capacidades esenciales comprometidas (M1–M6 + ingesta + API v1 + UI)
están implementadas y verificadas con evidencia real, y no hay defectos
bloqueantes abiertos. Quedan **condiciones concretas, verificables y no
bloqueantes** antes de un **CERRADO** formal: commitear esta actualización
documental y etiquetar la versión, y decidir formalmente el estado de los
pendientes declarados (T-001/T-003/T-005/T-011/T-013) como *fuera de alcance* o
*versión posterior*.

---

## Alcance evaluado

- **Propósito:** herramienta de escritorio para acomodar, validar, comparar y
  cruzar hojas de cálculo de forma repetible (definir un modelo una vez y
  reejecutarlo con datos nuevos).
- **Capacidades esenciales comprometidas:** ingesta local (scanner, admisión,
  cola con 9 estados, lease atómico); pipeline CSV→DuckDB→Parquet versionado;
  publicación atómica y reconciliación; API v1 sobre socket Unix (20 operaciones);
  app de escritorio tkinter con 7 vistas; y el motor de modelado M1–M6 (inspect, model,
  transformar, cruzar, repetir/diff, UI).
- **Versión:** `0.1.0` (sin tag Git).
- **Entornos:** Linux, Python ≥3.11,<3.14 (verificado en 3.12.13), DuckDB 1.5.6,
  systemd de usuario.
- **Nivel de madurez esperado:** **Operativo controlado** (predeterminado; el
  propósito es herramienta local de un solo operador, no servicio de producción).

---

## Evaluación de los 16 criterios

| # | Criterio | Estado | Evidencia |
|---|---|---|---|
| 1 | Funcionalidad | **Cumple** | E2E real: `read_any`→`profile_table`→`store`→`rules`→`run.execute`→`run.record` → `[['Cusco',120.0],['Cusco',340.0]]`, huella de 2 filas. API sobre socket real (20 operaciones), UI tkinter con 7 vistas. |
| 2 | Arquitectura | **Cumple** | 6 ADRs (014–019); import de `motor`, `motor.api.routes`, `motor.model.engine`, `motor.worker` sin ciclos. DuckDB único motor (ADR-002), SQLite autoridad de estado (ADR-005). |
| 3 | Seguridad | **Cumple** | SAST propio (`semgrep`, `bandit`) → **0 vulnerabilidades explotables**; SQLi refutados con ataques reales. Dependencias: 5 avisos, todos en `chromadb` (sin corrección), riesgo aceptado. Socket `0660` con aceptación explícita (ADR-016). |
| 4 | Fiabilidad | **Cumple** | Recuperación de leases (`requeue_expired`), estados atrapados cerrados (T-009), commit idempotente por `job_key`, reconciliación idempotente; errores traducidos a códigos cerrados (T-021). |
| 5 | Integraciones | **Cumple** | DuckDB, SQLite, systemd (unidades reales verificadas, T-014). Codex Security/DeepInfra: no viable (incompatibilidad plugin↔proveedor, no del proyecto). |
| 6 | Persistencia | **Cumple** | Parquet versionado + `manifest.json` como marca física de commit (ADR-004); claims y retención en SQLite; GC efectivo. |
| 7 | Rendimiento | **Cumple** | Límites acotados (`MAX_ATTEMPTS=3`, `MAX_REQUEST_BYTES=16 MiB`, retención); correctitud, no calibración de carga. Propósito local. |
| 8 | Compatibilidad | **Cumple** | `requires-python >=3.11,<3.14`; verificado en 3.12.13. systemd `Type=exec` en Linux. |
| 9 | Reproducibilidad | **Cumple** | `uv lock --check` OK (171 paquetes resueltos); `uv sync --frozen` documentado; wheel incluye `motor` y `defaults.toml`. |
| 10 | Validación adversarial | **Cumple** | Pruebas de traversal de rutas, cuerpo vacío, filtro de estado vacío, nombre de columna con intento de inyección, ZIP corrupto, entrada no confiable (F-01/F-02/F-03). |
| 11 | Regresiones | **Cumple** | Cada defecto corregido tiene prueba con nombre y comentario «Regresión»: BH-001 (×3), BH-004, F-03, joins con punto, xlsx corrupto, worker. |
| 12 | Observabilidad | **Cumple** | `log.py` emite una línea JSON por ciclo/job a journald; nunca valores de datos (prueba con centinela). |
| 13 | Consistencia de entrega | **Parcial** | Metadatos de distribución alineados y build validado (`yachay-local-data-modeling`, autor Git, README, Python 3.11); `uv.lock` actualizado. **Sin tag Git**; el árbol local sigue sin commit. |
| 14 | Documentación | **Cumple** | README, 6 ADRs, arquitectura, diagramas, OpenAPI, revisión de API, tracker con tareas y pendientes clasificados. |
| 15 | Control de alcance | **Cumple** | Los pendientes declarados (T-001/T-003/T-005/T-011/T-013) no bloquean las capacidades M1–M6 ni la ingesta/API/UI. No hay funciones nuevas que impidan cerrar. |
| 16 | Aceptación final | **Cumple en el árbol local** | `./scripts/test.sh`: **477 pruebas, 7 omitidas, 100 % de cobertura (2242 sentencias, 508 ramas), Ruff limpio, EXIT=0**. Resultado del 2026-11-15 sobre `ea0962a` más los cambios locales listados abajo; falta repetirlo si el commit final difiere. |

---

## Hallazgos y bloqueos

**No hay defectos bloqueantes abiertos tras esta reparación.** El baseline en
`ea0962a` falló el gate: 476 pruebas, 7 omitidas, Ruff limpio y 99 % de cobertura
(2 sentencias sin cubrir en `src/motor/api/types.py:44-45`). La inspección encontró
además que, si ya existía el archivo `.part`, `Body.save_atomic()` lo borraba al
fallar `os.open`; ese archivo actúa como lock contra una segunda carga. La
reparación ahora solo limpia el `.part` cuando esta llamada logró crearlo, y una
regresión exige que un lock ajeno permanezca intacto. Una prueba adicional cubre
la carrera de desaparición durante la limpieza. El gate final pasó con 477 pruebas
y 100 %.

En una pasada intermedia del gate sobre este mismo árbol, unittest abortó con
`Tcl_AsyncDelete: async handler deleted by the wrong thread` (exit 132). La
repetición inmediata terminó correctamente con el resultado anterior. No se
determinó la causa de esa falla intermitente; se registra como inestabilidad de
verificación pendiente, no como defecto confirmado del producto.

| ID | Severidad | Capacidad afectada | Impacto operativo | Evidencia | ¿Bloquea? |
|---|---|---|---|---|---|
| BH-001 | Crítica | Motor de reglas (M3) | Corrupción silenciosa: `map`+`cast` sobre una columna anulaba valores | Reproducido y **corregido**; 3 regresiones | **No** (resuelto) |
| BH-004 | Media | `rules.expression_for` | `domain` era no-op silencioso | Reproducido y **corregido**; 2 regresiones | **No** (resuelto) |
| BH-002 | Alta | Gate de cobertura | El gate no detectaba BH-001 | **Corregido**: 5 regresiones, 457 pruebas | **No** (resuelto) |
| API-08 | Media | Carga atómica de archivos | El cleanup borraba un `.part` ajeno al fallar `O_EXCL`, liberando el lock de otra carga | Reproducido desde `ea0962a`; corregido y cubierto por regresión | **No** (resuelto) |
| TEST-01 | Baja | Gate local | Una ejecución abortó intermitentemente en Tcl con exit 132 | Una repetición inmediata pasó; causa no determinada | **No** (inestabilidad de verificación) |
| C-01 | Baja | Entrega (§13) | Corrección e informe sin commit; versión sin tag | `git status` / `git describe` | **No** (condición de cierre) |

---

## Riesgos aceptados y pendientes

| Riesgo | Severidad | Mitigación | Aceptación |
|---|---|---|---|
| `chromadb 1.5.9` (5 avisos) | Media | Transitiva dura de `crewai`; no la importa `src/motor`; sin corrección publicada | Explícita en `docs/seguimiento-local.md` |
| Socket `0660` | Baja | Vive en `$XDG_RUNTIME_DIR/motor/` (0700); `panda` único miembro del grupo | Explícita en ADR-016 y `REVIEW-API-V1.md` |

### Pendientes funcionales (validados: fuera de alcance, **no** incorregibles)

Verificación completa en `audit/validacion-pendientes.md`. **Ninguno es un
bloqueo técnico**: los cinco son corregibles, pero exigen cambiar el contrato o
introducir una capacidad nueva, y **ninguno causa fallo funcional observable**
hoy.

| Pendiente | ¿Corregible? | ¿Fallo hoy? | Por qué fuera de alcance |
|---|---|---|---|
| T-001 (ejecución real del Flow) | Sí — `crewai` construye `deepinfra/deepseek-ai/DeepSeek-V4-Flash` | No | Es un **generador de documentación**, no una capacidad del producto; el doc objetivo ya existe. |
| T-003 (generador de artefactos) | Sí — herramienta nueva pequeña | No | **Función nueva**, no corrección; `docs/modelado/` ya está versionado. |
| T-005 (crash consistency de dlt) | Sí — adoptar dlt | No | dlt **no se adoptó** (dictamen: no orquestador); no hay nada que validar. |
| T-011 (heartbeat entre lotes) | Sí — DuckDB tiene `fetch_record_batch` | No | Margen ≫ 20×: 560 MB → 1.49 s con TTL de 30 s; datos reales son hojas de ~100 filas. |
| T-013 (`WatchdogSec`) | Sí — **solo tras T-011** | No | Cablearlo **hoy crearía fallos** (mataría CSVs lentos). `Restart=on-failure` ya cubre la caída. |

**Nota sobre el criterio 3 (Seguridad):** no depende de T-013. Los controles
reales son `Restart=on-failure`, hardening systemd y ausencia de listener TCP; el
watchdog es una mejora de *liveness*, no un control de seguridad bloqueante.

---

## Mejoras fuera del cierre

- Etiquetar la versión (`git tag v0.1.0`) al commitear las correcciones.
- Reportar el namespacing de tools del plugin de Codex Security (bloquea el
  escaneo con proveedores OpenAI-compatible no-OpenAI).
- Calibrar los parámetros de retención (§12) cuando exista política de datos.
- Cruces en escritorio: sí, se recomiendan por el propósito principal del producto.
  La siguiente entrega debe definir cómo mapear cada entidad a su archivo y cómo
  aplicar reglas por fuente antes de exponer CRUD de cruces y ejecución en API/UI: hoy
  `POST /runs` acepta un solo `source` y las reglas no tienen alcance por entidad.
- CI: no se configura todavía. En esta revisión no hay remoto Git ni workflow
  de CI, y el perfil declarado es de operador local único. Reconsiderarlo cuando
  exista un remoto o colaboración regular; entonces ejecutar `uv sync --frozen`
  y `./scripts/test.sh` en el pipeline.
- Metadatos del paquete: se reemplazan el nombre documental y el autor de
  plantilla por `yachay-local-data-modeling` y la identidad Git configurada;
  Ruff apunta a Python 3.11, el mínimo declarado, y el README queda declarado como metadata del paquete.
  `uv lock --check`, `uv build` y el gate local pasan con los metadatos
  actualizados.

---

## Condiciones de cierre

Para pasar de **CIERRE CONDICIONADO** a **CERRADO**, con evidencia de aceptación:

1. **Committear la reparación, sus regresiones y este informe.**
   *Evidencia:* `git status` limpio y el commit final conserva el resultado del
   gate registrado para este árbol.
2. **Repetir el gate si el árbol cambia antes del commit final.** La ejecución
   actual de `./scripts/test.sh` pasó con **477 pruebas / 7 omitidas / 100 % /
   Ruff limpio / EXIT=0**.
   *Evidencia:* ejecución del 2026-11-15 sobre `ea0962a` más las modificaciones
   locales descritas en esta evaluación.
3. **Fijar la versión**: crear tag `v0.1.0` (o decidir explícitamente no
   etiquetar).
   *Evidencia:* `git describe --tags` no vacío, o decisión registrada.
4. **Formalizar los pendientes**: registrar T-001/T-003/T-005/T-011/T-013 como
   *fuera de alcance de esta versión* (validados como corregibles pero no
   necesarios; ver `audit/validacion-pendientes.md`), ya no como «incorregibles».

---

## Recomendación final

**Congelar el alcance funcional M1–M6 + ingesta + API v1 + UI.** Las capacidades
comprometidas cumplen su contrato con evidencia real y, tras corregir el defecto
del lock de carga descubierto al ejecutar el gate sobre `ea0962a`, sin bloqueos
técnicos conocidos.

El siguiente paso concreto es **cerrar las 4 condiciones** de la sección
anterior (commit + gate + versión + formalizar pendientes); cumplidas, el
dictamen pasa a **CERRADO** y el desarrollo de este alcance debe detenerse.

No hay motivo técnico para seguir añadiendo verificación: el contrato está
demostrado y los riesgos residuales son aceptables y explícitos.
