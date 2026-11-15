# Registro E2E: escritorio Yachay contra API local

## Estado de ejecución

- Estado: **hallazgos corregidos y verificados**.
- Fecha: 2026-11-15.
- Alcance: lanzar la aplicación Tk real bajo Xvfb, conectarla a una API real
  protegida por `YACHAY_API_TOKEN`, y recorrer funciones de usuario sobre una
  raíz temporal aislada.
- El token no debe escribirse en este documento, en logs, argumentos de proceso
  ni archivos del repositorio. Pasarlo solo por entorno de los procesos API y
  escritorio; no imprimirlo.
- No usar la raíz de `/home/panda/Yachay/data` para los fixtures del recorrido.

## Herramientas usadas

1. `xvfb-run` para el display Tk aislado.
2. `YachayApp` real en Tk; sus botones se accionaron con `invoke()`, las tablas
   con la selección real de Treeview y las teclas con eventos Tk. No se usó
   `xdotool` en este recorrido.
3. `build_server` real en un hilo local, con socket Unix, token temporal y
   almacenamiento temporal. El conector de producción envió las peticiones y
   se verificaron los estados y manifests por API.
4. Las suites `unittest` registradas abajo como regresión complementaria.
   Playwright, Chrome DevTools y el escáner web no aplican a estos widgets Tk.

## Evidencia ya obtenida (no repetir)

Estas son pruebas previas al recorrido E2E; no se repitieron durante él:

- `xvfb-run -a env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:desktop .venv/bin/python -m unittest tests.unit.test_desktop_ui`
  — **55 tests OK**.
- Suite de widgets, callbacks, refresco y cierre focalizada bajo Xvfb —
  **21 tests OK** (subconjunto de los 55 anteriores).
- `tests.unit.test_desktop_ui.ClientAgainstRealApiTests` — **11 tests OK**;
  conectan el cliente a un servidor real sobre socket Unix, pero ejercen el
  cliente directamente y no toda la interfaz gráfica.
- Prueba adicional con Tk visible: siete atajos `Ctrl+1..7`, botón de
  navegación, `F5` y `Ctrl+R` — **OK**.

## Recorrido E2E original (antes de las correcciones)

Se usó una raíz temporal aislada (`/tmp/yachay-e2e-desktop-api`) y un CSV con
dos filas (`id,nombre`, Ana y Bea). El token se introdujo de forma oculta,
solo vivió en los procesos de API/UI y no se guardó en archivos ni resultados.
El directorio temporal se eliminó tras completar el recorrido.

| Paso | Resultado | Evidencia funcional |
|---|---|---|
| API protegida y estado del servicio | PASS | API respondió 200 con Bearer; el panel mostró «En línea». |
| Carga y perfilado desde control Tk | PASS | CSV aceptado, dos filas perfiladas; el modelo heredó el nombre de fuente. |
| Creación de modelo desde UI | PASS | Confirmación visible; API devolvió modelo 1 y UI propagó contexto a Ejecución/Resultados. |
| Dos ejecuciones desde UI | PASS | UI mostró runs 1 y 2 con dos filas; API confirmó el historial. |
| Historial y comparación desde UI | PASS | Selección de dos runs produjo «Sin cambios»; API diff confirmó `changed=false`. |
| Publicación del worker, catálogo y manifest | PASS con hallazgo | Worker publicó el CSV; catálogo mostró el dataset y la ventana desplegó manifest confirmado por API. Ver FE-E2E-01. |
| Perfil de CSV binario y estado de error | PASS con observación | El perfilador mostró «0 filas, 1 columnas» como éxito; el worker después lo rechazó como `InvalidInputException`. Ver FE-E2E-02. |
| Reintentar trabajo fallido desde UI | PASS | Botón habilitado en `failed`; la API confirmó transición a `queued`. |
| Cancelar trabajo en cola desde UI | PASS | Botón habilitado en `queued`; la API confirmó `cancel_requested=true`. |
| Cierre | PASS | Ventana/API cerradas y socket eliminado. |

## Recorrido E2E después de corregir

Un segundo recorrido aislado autenticó la API, ejerció la UI Tk y comprobó los
resultados con el servidor real:

- Perfiló un CSV Latin-1 con dos filas, creó un modelo, ejecutó dos veces y
  comparó ambas ejecuciones sin cambios.
- El worker publicó el primer CSV; seleccionar el dataset activó **Ver
  manifest** sin abrir la ventana automáticamente. Al pulsar el botón, la UI
  obtuvo el manifest por API.
- Perfiló la entrada binaria de cero filas y el worker procesó la misma entrada
  como `committed` con cero filas; ya no aparece el `InvalidInputException` que
  ocurría por la diferencia de codificación.
- Resultado: **PASS**. Los procesos usaron una raíz temporal; el token no se
  guardó ni apareció en la salida.

### Hallazgos encontrados y correcciones

- **FE-E2E-01 — corregido:** la selección ahora actualiza el estado y la ayuda
  del botón; seleccionar no abre el manifest. El botón se activa y es la acción
  que abre el manifest.
- **FE-E2E-02 — corregido:** el worker detecta incrementalmente si el CSV usa
  UTF-8 o necesita fallback Latin-1, igual que el perfilador. El pipeline
  conserva lectura por lotes y no carga el archivo completo para elegir
  codificación.

### Comandos y artefactos

- Suite de UI bajo Xvfb: **55 OK**.
- Pruebas focalizadas de widgets/puente/cierre bajo Xvfb: **21 OK** (subconjunto
  de la suite de 55).
- Integración cliente/API real: **11 OK**.
- Baseline antes de reparar: `./scripts/test.sh` — Ruff OK, 474 tests OK;
  `uv build --offline --out-dir /tmp/yachay-build-baseline` — sdist y wheel OK.
- Reproducción FE-E2E-01: `button=disabled`, ayuda inicial obsoleta y llamada
  involuntaria a manifest tras seleccionar.
- Reproducción FE-E2E-02 con bytes no UTF-8: perfilador mostró `0 filas`; worker
  creó un trabajo `failed` por `InvalidInputException`.
- Regresiones: selección/acción Tk **1 OK**; pipeline CSV **4 OK**; integración
  UI → perfil → worker → API con CSV Latin-1 **PASS**, trabajo `committed` y
  dataset publicado.
- Gate tras reparar: `xvfb-run -a env PYTHONDONTWRITEBYTECODE=1
  ./scripts/test.sh` — Ruff OK, **476 tests OK, 7 omitidos**.
- E2E: API autenticada, ventana Tk, worker y almacenamiento temporales en el
  mismo proceso de prueba (API en hilo); controles mediante Tk (`invoke` y
  selección), y atajos mediante eventos. La API fue el servidor real en socket,
  no un mock. La UI no se lanzó como proceso separado ni se usó automatización
  por coordenadas; el arranque independiente del ejecutable queda fuera de este
  alcance.
- Checkpoint interrumpido por FE-E2E-01 y luego reanudado: se reutilizó la misma
  raíz temporal y se consultaron datos existentes antes de crear acciones, sin
  duplicar modelo, carga ni ejecuciones.
- Ningún token quedó en el repositorio, registro, comandos ni archivos; la raíz
  temporal fue eliminada al concluir.

## Reanudación

El recorrido concluyó y no requiere reanudación. Si se repite tras corregir los
hallazgos, usar una nueva raíz temporal y evitar reejecutar las suites previas
salvo que haya cambios relacionados.

## Resultados E2E

Resultado final: **ambos hallazgos corregidos, pruebas de regresión y E2E
posterior PASS**. El cliente y la UI usaron la API autenticada; ninguna key se
incluye en este registro.
