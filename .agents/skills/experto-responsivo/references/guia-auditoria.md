# Guía de auditoría responsive y adaptive

## Alcance mínimo

Declarar versión o revisión examinada; pantallas, rutas y componentes cubiertos; estados (normal, largo, error, carga, vacío, modal, menú y formulario); navegador/SO efectivos; método (estático, render emulado o dispositivo real); dimensiones; zoom; orientación; y exclusiones.

Una matriz finita no demuestra universalidad. `CONFORME EN ALCANCE PROBADO` significa que todas las pruebas declaradas pasaron dentro de ese alcance.

## Matriz basada en riesgo

Para web/PWA usa como muestra inicial anchos CSS px de 320, 360, 390, 430, 768, 1024, 1280, 1440 y 1920, más los puntos propios del producto. Añade alturas reducidas, vertical/horizontal y el ancho exacto antes y después de cada breakpoint o punto de fallo. Es una muestra, no un estándar ni una garantía.

En web comprueba ampliación de texto al 200% cuando aplique y reflow equivalente a 320 CSS px para contenido de lectura horizontal; para contenido desplazado horizontalmente, considera el caso equivalente de 256 CSS px de altura. Un viewport de 320 px no equivale por completo al zoom del navegador.

Abre y cierra menús, diálogos, popovers y selectores; prueba teclado, foco visible, contenido largo, errores de formulario, scroll, sticky/fixed, carga asíncrona, cambio de orientación y teclado virtual. Explora continuamente entre muestras y junto a los breakpoints.

## Checks por plataforma

### Web, PWA y webview

- Viewport móvil correcto; zoom y ampliación no restringidos indebidamente.
- Contenido esencial visible, accesible y operable sin scroll horizontal de página injustificado; mapas, diagramas y tablas bidimensionales requieren valorar su excepción.
- Flex/Grid, contenedores, imágenes, iframes y textos largos sin pérdida ni solapamiento. Evalúa `min-width: 0`, `overflow-wrap`, `minmax()` y alternativas solo cuando el comportamiento lo justifique.
- Alturas dinámicas, barras del navegador, safe areas y teclado virtual no ocultan controles.
- Menús, dropdowns, tooltips y modales funcionan en viewport pequeño, con foco/teclado y puntero táctil; no dependen solo de hover.
- Verifica zoom, reflow, espaciado de texto y objetivos interactivos contra los criterios aplicables. Mide CLS únicamente si existe instrumentación; resize por sí solo no es CLS.
- Considera internacionalización, expansión de texto y RTL cuando correspondan.

### Android, iOS y multiplataforma

Evalúa espacio real de ventana, orientación admitida, safe areas/cutouts, navegación del sistema, teclado, text scaling/Dynamic Type, objetivos táctiles, multiwindow, plegables y conservación del estado. Distingue dp/pt de CSS px. Justifica la adaptación de listas, tablas y formularios por su contenido.

### Desktop nativo o híbrido

Comprueba tamaño mínimo e intermedio de ventana, resize, snap, restauración, paneles, menús, diálogos, textos ampliados y controles fuera de la región visible. Cuando sea posible, prueba escalado por monitor y traslado entre DPI distintos. En Electron/Tauri aplica además los checks web a su webview; no asumas equivalencia completa entre navegadores.

## Normativa y referencias

Aplica el requisito exacto y sus excepciones, separándolo de recomendaciones de plataforma y preferencias de diseño:

- WCAG 2.2 1.4.4 (AA): ampliación de texto hasta 200% sin pérdida de contenido o función, con sus excepciones.
- WCAG 2.2 1.4.10 (AA): reflow sin scroll bidimensional en los casos aplicables de 320 CSS px de ancho o 256 CSS px de alto; contenido cuyo significado o uso exige dos dimensiones queda exceptuado.
- WCAG 2.2 1.4.12 (AA): espaciado de texto; verifica pérdida o superposición, no solo propiedades CSS.
- WCAG 2.2 1.3.4 (AA): no restringir orientación salvo que sea esencial.
- WCAG 2.2 2.5.8 (AA): objetivo mínimo de 24×24 CSS px o excepción válida; no declares incumplimiento solo por ser menor.
- WCAG 2.2 2.5.5 (AAA): objetivo de 44×44 CSS px; no lo presentes como requisito AA.

Material Design y Apple HIG son referencias de plataforma, no automáticamente normas WCAG. CLS es estabilidad visual y requiere medición; no prueba responsividad por sí solo.

Fuentes primarias para validar requisitos y excepciones: <https://www.w3.org/TR/WCAG22/> y <https://www.w3.org/WAI/WCAG22/quickref/>. Para CLS: <https://web.dev/articles/cls>. Si una guía depende de versión, consulta la fuente y anota la fecha.

## Severidad contextual

- **Crítica**: bloquea un flujo esencial o deja controles imprescindibles inalcanzables sin alternativa en un entorno soportado.
- **Alta**: afecta una familia relevante o confirma incumplimiento AA aplicable con impacto funcional claro.
- **Media**: deterioro perceptible y reproducible con alternativa viable, fricción o desalineación relevante.
- **Baja**: pulido, inconsistencia menor o recomendación no obligatoria sin pérdida funcional.

Un riesgo estático puede tener impacto potencial alto, pero conserva el estado `INDICIO ESTÁTICO` hasta reproducirse.

## Dictamen y cierre

- **CONFORME EN ALCANCE PROBADO**: pruebas declaradas ejecutadas y aprobadas, sin defectos abiertos.
- **CONFORME CON OBSERVACIONES**: sin bloqueos o incumplimientos confirmados; quedan defectos menores o mejoras.
- **NO CONFORME**: defecto crítico/alto confirmado o incumplimiento aplicable confirmado.
- **EVIDENCIA INSUFICIENTE**: no hay base suficiente para pronunciar conformidad; los indicios siguen siendo riesgos.

Un hallazgo solo queda cerrado cuando la causa está razonablemente localizada, el cambio está aplicado o propuesto, la reproducción original deja de fallar cuando hay runtime, se cubren regresiones en anchos contiguos, zoom, estados y plataformas afectadas, y la evidencia queda registrada. Sin retest: `CORRECCIÓN PROPUESTA, NO VALIDADA`.
