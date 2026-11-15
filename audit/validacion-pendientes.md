# Validación de pendientes — ¿son incorregibles o fuera de alcance?

- **Fecha:** 2026-11-12
- **Pregunta:** validar si T-001/T-003/T-005/T-011/T-013 **no se pueden corregir**
  (bloqueo técnico real) o si son **corregibles pero fuera de alcance**.
- **Método:** evidencia empírica por pendiente (no se acepta la afirmación del
  tracker sin comprobarla).

## Conclusión

**Ninguno es técnicamente incorregible.** Los cinco son **corregibles pero fuera
de alcance**: exigen cambiar el contrato comprometido o introducir una capacidad
nueva, y **ninguno causa fallo funcional observable hoy** para el propósito real
(local, un operador, hojas de cálculo). No hay bloqueo técnico; hay decisión de
alcance.

Presentarlos como «bloqueados» era impreciso. El estado correcto es
**fuera de alcance de esta versión**, con la evidencia de abajo.

---

## T-001 — Ejecución real del Flow (documentación)

| Aspecto | Evidencia |
|---|---|
| Qué es | Flow CrewAI de **documentación**: 5 métodos-agente que redactan/verifican/entregan el documento de arquitectura a partir del canónico embebido. |
| Proveedor fijo | `"llm": "openai/gpt-4o"` ×5 en `flow.json`. |
| ¿Corregible? | **Sí.** `crewai 1.15.39` construye `LLM(model='deepinfra/deepseek-ai/DeepSeek-V4-Flash')` sin error. Basta cambiar el `llm`. El propio `flow.json` ya menciona DeepInfra. |
| Por qué fuera de alcance | Es un **generador de documentación**, no una capacidad del producto. El doc objetivo (`docs/modelado/ARQUITECTURA-MODELADO.md`) ya existe. Sus 20 pruebas validan todo el contrato **estático** (carga, cadena lineal, plantillas, `emit`/`router`, estado). |
| Coste de corregir | Cambiar 5 `llm` + una ejecución real con facturación de tokens. **No aporta a las capacidades M1–M6 ni a la ingesta/API/UI.** |

**Veredicto: corregible, fuera de alcance.** Ejecutarlo no mejora ninguna
capacidad comprometida.

---

## T-003 — Generador de artefactos de documentación

| Aspecto | Evidencia |
|---|---|
| Qué falta | Un generador que escriba `docs/generated/` desde la fuente canónica. |
| Estado real | `docs/generated/` está en `.gitignore`; **no existe código de generador** (`grep generated src/` → vacío). La fuente canónica vive en `flow.json`. |
| ¿Corregible? | **Sí**, es una herramienta nueva pequeña. |
| Por qué fuera de alcance | Es una **función nueva** (publicación de docs), no una corrección. No afecta al contrato funcional ni de seguridad. `docs/modelado/` ya está versionado a mano. |

**Veredicto: corregible, fuera de alcance** (mejora, no defecto).

---

## T-005 — dlt (adopción del adaptador)

| Aspecto | Evidencia |
|---|---|
| Estado | `dlt` **no es dependencia** (`pyproject`/`uv.lock` sin `dlt`; `import dlt` → `ModuleNotFoundError`). |
| Evaluación | `docs/evaluacion-dlt-duckdb.md`: «APTO CON CONDICIONES» como adaptador **interno**, nunca como orquestador. |
| ¿Corregible? | **Sí**, sería adoptar el adaptador. |
| Por qué fuera de alcance | La evaluación **decidió no adoptarlo** (tensión con SQLite-autoridad y manifest-commit). La T-005 pide «crash consistency y cargas representativas» de una dependencia **que no se usa**. |

**Veredicto: fuera de alcance / no aplica** (dlt no se adoptó; no hay crash
consistency que validar de algo que no está en el sistema).

---

## T-011 — Heartbeat real entre lotes

| Aspecto | Evidencia |
|---|---|
| Situación | El pipeline es **un disparo** (`read_csv_auto`); el worker late 2 veces por job (antes y después del pipeline), no entre lotes. |
| Umbral | `lease_ttl_s = 30.0`. Medido: CSV de **560 MB / 20M filas → 1.49 s**; 3M filas → 0.18 s. Para superar 30 s haría falta un archivo de **varios GB con disco lento**. |
| ¿Corregible? | **Sí.** DuckDB expone `fetch_record_batch`/`fetchmany` (streaming): se puede leer por lotes y latir entre ellos. |
| Por qué fuera de alcance | Corregirlo **cambia el contrato del pipeline** (de transformación atómica a por lotes) y añade complejidad por un escenario que los datos reales (hojas de ~100 filas) no alcanzan. |

**Veredicto: corregible, fuera de alcance.** Es sobreingeniería para el
propósito; el límite actual es correcto con margen enorme.

---

## T-013 — `WatchdogSec` + `sd_notify`

| Aspecto | Evidencia |
|---|---|
| Situación | Unidades `Type=exec` **sin** `WatchdogSec`; ausencia **deliberada y documentada** en `docs/systemd/README.md:54` y `ADR-018`. |
| Motivo declarado | El pipeline es de un solo disparo: **no hay puntos de latido** donde emitir `WATCHDOG=1`. Un watchdog mataría CSVs lentos y, con `MAX_ATTEMPTS=3`, los volvería fallos deterministas. |
| ¿Corregible? | **Solo si se resuelve antes T-011** (que crea las fronteras de lote). |
| Por qué fuera de alcance | Depende de T-011. Añadirlo hoy **introduce un fallo**: el watchdog mataría jobs legítimos. `Restart=on-failure` ya cubre la caída del proceso. |

**Veredicto: corregible (dependiente de T-011), fuera de alcance.** Cablearlo
ahora sería un defecto, no una mejora.

---

## Matriz de decisión

| Pendiente | ¿Bloqueo técnico? | ¿Corregible? | ¿Causa fallo hoy? | Estado correcto |
|---|---|---|---|---|
| T-001 | No | Sí (cambiar `llm` a DeepInfra) | No | Fuera de alcance |
| T-003 | No | Sí (herramienta nueva) | No | Fuera de alcance |
| T-005 | No | Sí (adoptar dlt) | No (no se usa) | Fuera de alcance / N/A |
| T-011 | No | Sí (leer por lotes) | No (margen ≫ 20×) | Fuera de alcance |
| T-013 | No | Sí (tras T-011) | No (y cablearlo **crearía** fallos) | Fuera de alcance |

## Implicación para el dictamen de cierre

La condición 4 se reformula: **no** hay que demostrar que son «incorregibles»
(no lo son), sino **registrarlos como pendientes de versión posterior** con la
justificación de alcance. El criterio 15 (Control de alcance) sigue
**Cumple**: ninguno impide cerrar las capacidades comprometidas.

Y es importante: **el criterio 3 (Seguridad) no depende de T-013.**
`Restart=on-failure` + hardening systemd + la ausencia de listener TCP son los
controles reales; el watchdog es una mejora de liveness, no un control de
seguridad bloqueante.
