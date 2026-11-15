# Yachay Desktop

Interfaz de escritorio para el **motor de datos local Yachay**. Mínima, ligera y
sin dependencias: usa **tkinter/ttk** de la biblioteca estándar de Python.

> Es un **cliente** de la API v1, no parte del motor. Habla por el socket Unix
> (`<root>/data/motor.sock`, modo `0660`) y **solo** usa las operaciones que la
> API expone (ADR-016). Nunca reconcilia, nunca reclama leases y nunca ejecuta el
> pipeline.

## Requisitos

- Python ≥ 3.11 con **tkinter** (incluido en la mayoría de instalaciones de
  CPython en Linux).
- El motor en ejecución sirviendo la API:
  ```bash
  yachay api --root /ruta/al/proyecto
  ```
  (o `python -c "from motor.__main__ import main; main(['api'])"` según la
  instalación)

## Ejecutar

Tras instalar el paquete, el comando `yachay-desktop` queda disponible:

```bash
# Socket por defecto: <root>/data/motor.sock
yachay-desktop --root /ruta/al/proyecto

# Socket explícito
yachay-desktop --socket /ruta/motor.sock
```

Si la API está protegida con `YACHAY_API_TOKEN`, configura la misma variable
en el entorno donde se inicia `yachay-desktop`; el cliente la envía como bearer
en cada petición. Evita pasar el secreto como argumento de línea de comandos.

Durante el desarrollo también puede ejecutarse con `python -m yachay_desktop`
desde un entorno cuyo `PYTHONPATH` incluya `desktop/`.

Si no hay servidor X (entorno sin escritorio), la app lo informa y sale con
código 1 en lugar de fallar con una traza.

## Vistas

| Vista | Se alimenta de | Acciones |
|---|---|---|
| **Panel de estado** (`Ctrl+1`) | `GET /v1/health` + conteos de jobs/datasets | — |
| **Cargar archivo** (`Ctrl+2`) | `POST /v1/inputs?name=` + `GET /v1/profile?name=` | Depositar y perfilar |
| **Modelo** (`Ctrl+3`) | `GET /v1/models` + `POST /v1/models?name=&source=` | Crear modelo |
| **Ejecutar** (`Ctrl+4`) | `POST /v1/models/{id}/runs?source=` | Ejecutar y ver la huella |
| **Resultados** (`Ctrl+5`) | `GET /v1/models/{id}/runs` + `GET /v1/diff` | Comparar dos ejecuciones |
| **Cola de trabajos** (`Ctrl+6`) | `GET /v1/jobs` (filtro por estado) | Cancelar / Reintentar |
| **Catálogo de datasets** (`Ctrl+7`) | `GET /v1/datasets` | Ver manifest |

Siete vistas, en el orden de los atajos. Atajos de teclado: `Ctrl+1..7` cambia de
vista, `Ctrl+R` / `F5` refresca.

El Panel de estado incluye además un resumen de las capacidades del motor:
perfilado, modelado, transformación, cruces, repetición/diff, worker y
publicación. El resumen es informativo; solo muestra como accesibles las
operaciones que la API o la vista correspondiente exponen.

## Comportamiento fiel al motor

- **Subir no crea un trabajo**: el archivo entra en `inbox/` y el **escáner del
  worker** lo admite en su siguiente ciclo. La interfaz lo dice explícitamente.
- **Acciones por estado**: *Cancelar* solo se habilita en `claimed`, `queued`,
  `running`, `deferred`; *Reintentar* solo en `failed`, `quarantined`,
  `cancelled` (los mismos conjuntos que `motor/api/routes.py`). Los botones se
  **deshabilitan** con una explicación, en lugar de dejar que la API responda
  `invalid_state`.
- **Errores**: se ramifica por el **código cerrado** del envelope
  (`bad_request`, `not_found`, `invalid_state`, `payload_too_large`,
  `unavailable`, `internal_error`, `method_not_allowed`), con etiqueta en
  español.
- **Sin datos inventados**: los estados vacíos son vacíos reales; no hay filas
  de ejemplo ni métricas que el motor no exponga.

## Contrato visual

Usa los **tokens oficiales** de la sección 18 del documento canónico
(`theme.py` es la única fuente de color). El estado nunca se comunica solo por
color: cada estado lleva **símbolo + texto**. Fondo oscuro, paleta turquesa/oro,
espaciado en escala de 4 px, una sola familia tipográfica y tres pesos.

## Estructura

```
desktop/yachay_desktop/
├── __main__.py       # punto de entrada
├── app.py            # ventana y coordinación de la interfaz
├── client.py         # cliente HTTP sobre el socket Unix
├── views/            # vistas separadas por área funcional
├── ui_constants.py   # límites y catálogo compartidos
├── theme.py          # tokens oficiales (sección 18)
├── widgets.py        # tarjetas, badges de estado, botones
└── formatting.py     # fechas, identificadores, tamaños
```

## Limitaciones declaradas

- La interfaz **no** expone edición ni borrado de datasets/jobs, ni el contenido
  de los CSV, ni arranque del pipeline: la API no ofrece esas operaciones.
- El detalle de un trabajo (`run_id`) se muestra en el detalle; la lista de
  trabajos no lo incluye (así lo define la API).

## Pruebas

`tests/unit/test_desktop_ui.py` cubre la lógica sin depender de un motor vivo:
formateo, conversión de modelos, el envelope del cliente (con un socket Unix
falso) y la construcción de las siete vistas. Se ejecuta con el gate del
proyecto (`./scripts/test.sh`).

Las pruebas que necesitan Tk se **omiten** si no hay servidor gráfico
(`DISPLAY`), así que el gate pasa igual en un entorno headless. Para ejecutarlas
de verdad sin sesión gráfica:

```bash
xvfb-run -a env PYTHONPATH=src .venv/bin/python -m unittest tests.unit.test_desktop_ui
```
