# Auditoría Frontend Elite — Yachay Desktop (tkinter)

- **Nivel:** Estándar + comandos (estático + runtime/navegador)
- **Modo:** auditar y corregir
- **Superficie:** `desktop/yachay_desktop/` (`app.py` 1604, `client.py` 263,
  `widgets.py` 241, `theme.py` 108, `formatting.py` 33)
- **Fecha:** 2026-11-12

## Alcance

**Revisado:** `app.py`, `widgets.py`, `theme.py`, `client.py`, `formatting.py`,
`tests/unit/test_desktop_ui.py`, capturas de las 7 vistas a 1080x680 y 880x560.

**No revisado:** el motor (`src/motor/`), la API v1 (solo su contrato de
envelope), CI, empaquetado.

**Comandos ejecutados:**

- `.venv/bin/ruff check desktop` → `All checks passed!`
- `.venv/bin/coverage run -m unittest tests.unit.test_desktop_ui -v` → `17 tests OK`
- `xvfb-run -a .venv/bin/python scripts/audit_capture.py` → 9 PNG reales
- Sonda de fuentes Tk: `tkfont.Font(...).actual('family')`

**Métricas observadas:** cobertura de la suite desktop no medida de forma
aislada; sin bundle (no aplica). Sin CVEs medidos (sin red).

## Hallazgos

| ID | Sev. | Tipo | Ubicación | Resumen | Esf. | Certeza |
|---|---|---|---|---|---|---|
| FE-001 | Crítico | Bug | `theme.py:61-62` + `widgets.py:174,181` | DejaVu no resuelve en Tk → `fixed` → glifos de estado son tofu; se rompe «el estado nunca es solo color» | S | VERIFICADO |
| FE-002 | Alto | Bug | `widgets.py:44-89` + todas las vistas | «Actualizando…» se solapa con el título y con las acciones a anchos estrechos | S | VERIFICADO |
| FE-003 | Alto | Deuda | `app.py:520-541,869-894,1063-1085,1218-1240,1466-1486` | `stretch=False` + ancho suma > tarjeta → scrollbar horizontal permanente aunque sobre espacio | M | VERIFICADO |
| FE-004 | Medio | Bug | `app.py:1359-1367` | `Ejecutar`: `Text` de detalle vacío sin placeholder ni `rowconfigure` → caja en blanco que parece rota | S | VERIFICADO |
| FE-005 | Medio | Deuda | `app.py:508-526` | Encabezados `Treeview` centrados por defecto vs datos alineados a la izquierda | S | VERIFICADO |
| FE-006 | Medio | Bug | `app.py:1063-1071,1218-1226,1466-1472` | `stretch` inconsistente: solo una columna se estira, desalineando el resto | S | VERIFICADO |
| FE-007 | Medio | Deuda | `app.py:490-492,568-571` | Filtro de estado con valores crudos en inglés (`claimed`, `queued`…) frente a la UI en español | S | VERIFICADO |
| FE-008 | Bajo | Mejora | `app.py:173-180` | Ruta del socket sin `wraplength` coherente → corta «motor.sock» a mitad de palabra | S | VERIFICADO |
| FE-009 | Bajo | Deuda | `app.py:577-583` | `_load_jobs` con rama `if/else` idéntica (código muerto); `_state_label` reimporta dentro de la función | S | VERIFICADO |
| FE-010 | Bajo | Riesgo | `app.py:666-670,967-971` | `messagebox` sin `parent=`; el diálogo puede quedar detrás de la ventana | S | VERIFICADO |
| FE-011 | Bajo | Mejora | `widgets.py:209-241` | Tooltip solo por ratón: no accesible por teclado ni anunciado a lectores de pantalla | S | VERIFICADO |

## Detalle (Crítico y Alto)

### FE-001 — La resolución de fuente rompe el contrato de estado (Crítico)

**Ubicación:** `desktop/yachay_desktop/theme.py:61-62`,
`widgets.py:174,181,186`.

**Evidencia:**

```
$ .venv/bin/python -c "from tkinter import font; ..."
'DejaVu Sans'  -> 'fixed'
'Liberation Sans' -> 'fixed'
'Nimbus Sans L' -> 'nimbus sans l'
has DejaVu in Tk families: False
```

Tk 9.0.3 resuelve familias **solo** contra las 52 fuentes core X11; «DejaVu
Sans» no está entre ellas y Tk la colapsa silenciosamente a `fixed`. El `fixed`
bitmap no tiene `✓ ✕ ⚠ ⏸`, así que en las tablas los estados salen como tofu:

- Cola: `⋮⋮ Completado`, `⋮⋮ Fallido`, `⋮⋮ Diferido` (deberían ser `✓ ✕ ⏸`).
- Catálogo: `⯐ Íntegro` y `⯐ Manifest ausente` → **glifos idénticos**, cuando
  `theme.py:88-91` los exige «inequívocos y opuestos».

**Refutación intentada:** ¿faltan las fuentes en el sistema? No — `fc-list`
lista 22 caras DejaVu y `dpkg` confirma `fonts-dejavu-core`. El fallo es la
capa de resolución de Tk, no la instalación. ¿Afecta solo a xvfb? No: la sonda
usa el binding de fuentes del propio Tk, el mismo que usa la app en cualquier
sesión X11 con este build.

**Impacto:** la invariante central del diseño («el color nunca es la única
señal», sección 18) deja de cumplirse en las dos vistas de tabla, y dos estados
opuestos se vuelven indistinguibles.

**Corrección:** resolver la familia en tiempo de ejecución con una cadena de
preferencias verificada contra `tkinter.font.families()` (DejaVu → Noto →
Liberation → Nimbus → sans-serif base), y **validar los glifos de estado**:
elegir el primer candidato que resuelva y degrade los símbolos a ASCII
(`[ok] [x] [!] [||]`) si ni siquiera el elegido los tiene. Así el contrato se
sostiene en cualquier sistema, no solo donde DejaVu resuelva.

### FE-002 — Colisión del indicador de frescura (Alto)

**Ubicación:** `widgets.py:74-85` (`actions.pack(side="right")` con
`freshness.pack(side="left")` sin prioridad de espacio).

**Evidencia:** capturas `narrow-jobs.png` — «Actualizando…» se dibuja **encima**
de «Cola de trabajos»; `view-jobs.png` — la etiqueta se corta contra el
combobox «Filtrar:» («Actualizando‥»).

**Refutación intentada:** ¿es solo cosmético en ventanas pequeñas? No: ocurre
también a 1080 px en la vista de cola porque el combobox consume el ancho que
necesita la etiqueta. `pack` sin `fill`/`expand` reparte el espacio restante
sin garantizar ancho a la etiqueta.

**Impacto:** el usuario no ve la frescura (P-04), que es la señal de confianza
de todo el panel, y el título se vuelve ilegible.

**Corrección:** empaquetar la frescura **antes** que las acciones, reservarle su
ancho primero (`pack(side="right")` en orden invertido) y truncarla con
`…` en lugar de dejar que pise el título.

### FE-003 — Scrollbar horizontal permanente (Alto)

**Ubicación:** `app.py:520-541` (jobs), `869-894` (datasets), `1063-1085`
(profile), `1218-1240` (models), `1466-1486` (runs).

**Evidencia:** capturas `view-jobs/results/files/model/datasets.png` — en todas
aparece una scrollbar horizontal en reposo a 1080 px, con el contenido cabiendo
de sobra. Suma de `width` fijos > ancho de la tarjeta (~832 px útiles).

**Refutación intentada:** ¿el usuario la necesita para la columna «Motivo»? A
1080 px sí sobra contenido, pero la barra aparece porque los anchos suman de
más. No es un requisito, es un desajuste de medidas.

**Impacto:** ruido visual permanente, sensación de interfaz «desbordada» y una
fila de píxeles muerta bajo cada tabla.

**Corrección:** repartir el ancho disponible: dejar `stretch=True` en la columna
elástica, fijar el resto con `minwidth`, y calcular el total para que quepa en
el ancho típico (reducir `width` de las columnas de texto).

### FE-006 — `stretch` inconsistente (Medio, ligado a FE-003)

`stretch=column=="range"` / `=="key"` / `=="hash"` estira **una sola** columna
y deja el resto fijo, lo que desalinea la tabla al redimensionar. Se unifica en
la corrección de FE-003.

## Resultado (correcciones aplicadas)

Todas las correcciones se aplicaron en `desktop/yachay_desktop/`
(`theme.py`, `widgets.py`, `app.py`) y se verificaron con:

- `./scripts/test.sh` → **416 pruebas OK, cobertura 100 %, ruff limpio**.
- Recaptura de las 9 vistas: símbolos de estado distinguibles, sin solape de
  frescura, sin scrollbar horizontal en reposo, estado vacío explícito en
  «Ejecutar», encabezados alineados a la izquierda, filtro en español.

| ID | Estado | Comprobación |
|---|---|---|
| FE-001 | Corregido | `resolve_symbol`/`state_label` + resolución real de familia; símbolos ASCII verificados únicos |
| FE-002 | Corregido | `SectionTitle` en rejilla; frescura ya no invade el título (probado a 880 px) |
| FE-003 | Corregido | `make_table` con scrollbar horizontal auto-oculta; `xview=(0.0, 1.0)` en las 5 tablas |
| FE-004 | Corregido | «Detalle de la ejecución» con estado vacío y el panel crece |
| FE-005 | Corregido | Encabezados `anchor="w"` como los datos |
| FE-006 | Corregido | Una sola columna elástica por tabla, unificada en `make_table` |
| FE-007 | Corregido | `filter_options()`: etiquetas españolas → estado crudo |
| FE-008 | Corregido | Ruta del socket cortada por `/`, no a mitad de palabra |
| FE-009 | Corregido | `_render_jobs` sin rama muerta; imports hoisted |
| FE-010 | Corregido | `messagebox(..., parent=self)` en los 4 diálogos |
| FE-011 | Abierto | Tooltip por ratón; requiere decisión sobre `pyatspi` |

Se añadieron pruebas de regresión en `tests/unit/test_desktop_ui.py`
(`SymbolFallbackTests`): familia resuelta real, símbolos únicos tras el
fallback, `resolve_symbol` no vacío y mapa del filtro sin fugas de inglés.

## Roadmap

**Inmediato (S):** FE-001, FE-002, FE-004, FE-005, FE-007, FE-008, FE-009,
FE-010 — todos `esfuerzo S`, sin dependencias entre sí. **Aplicados.**

**Corto plazo (M):** FE-003 + FE-006 juntos (misma rejilla de columnas).
**Aplicados** vía el helper `make_table` compartido.

**Estructural:** FE-011 (tooltip accesible) si se prioriza accesibilidad de
lectores de pantalla; requiere decidir si se adopta `pyatspi` o se documenta el
límite de Tk.

## Preguntas abiertas

1. ¿El objetivo de despliegue garantiza DejaVu resuelta por Tk (p. ej. vía
   `TkDefaultFont` con `-xft`), o debe la app degradar sola? — Asumí **degradar
   sola**, que es lo verificable aquí; quedó implementado.
2. ¿La UI debe seguir 100 % en español también en los valores internos del
   filtro (FE-007)? — Resuelto con etiqueta española mapeada a estado crudo.
