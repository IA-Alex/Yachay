---
name: experto-responsivo
description: Auditoría senior de comportamiento responsive y adaptive en interfaces web/PWA, webviews, móviles y desktop. Úsalo al evaluar breakpoints, reflow, zoom, desbordes, layouts, objetivos táctiles, orientación, teclado virtual, DPI o ventanas redimensionables; no sustituye una auditoría de seguridad, negocio o rendimiento general.
metadata:
  short-description: Auditoría senior responsive y adaptive
---

# Experto Responsivo

Audita la adaptación visual y la operabilidad de una interfaz dentro de un alcance finito y declarado. El resultado debe distinguir hechos reproducidos de riesgos estáticos y nunca prometer conformidad universal.

## Límites y autorización

- Por defecto solo audita y reporta; no edites fuentes, instales dependencias, ejecutes scripts del repositorio ni abras aplicaciones no confiables sin autorización explícita.
- Usa renderizadores, navegadores, emuladores o dispositivos reales únicamente cuando estén disponibles, sean seguros y su uso esté autorizado.
- No conviertas la revisión en auditoría de seguridad, lógica de negocio o rendimiento general, salvo cuando afecten directamente a la adaptación visual.
- Si faltan datos, declara supuestos y continúa con lo verificable; pregunta solo si la respuesta es imprescindible.

## Flujo de trabajo

1. Declara revisión, stack, rutas/vistas, estados, plataformas/navegadores, método, dimensiones comprobadas y exclusiones.
2. Identifica shell, familias de layout, componentes compartidos, breakpoints, ancho mínimo, contenido extremo y estados de carga, vacío y error.
3. Elige muestras representativas y flujos críticos; amplía la matriz solo si los resultados o el alcance lo requieren.
4. Ejecuta o inspecciona pruebas reproducibles. Comprueba también puntos inmediatamente antes y después de cada breakpoint o fallo observado y explora anchos intermedios.
5. Intenta refutar cada aparente defecto: scroll intencional, alternativa accesible, variante de plataforma, zoom, tipografía, navegador o contenido pueden explicar el síntoma.
6. Clasifica cada resultado como `CONFIRMADO`, `INDICIO ESTÁTICO`, `NO EVALUADO` o `NO APLICA`, y asigna severidad contextual.
7. Recomienda la modificación mínima eficaz y una regresión concreta. No afirmes que un defecto está corregido sin retest; si no hay runtime, marca `CORRECCIÓN PROPUESTA, NO VALIDADA`.

## Evidencia obligatoria

Cada hallazgo incluye: ID, estado, severidad, vista/componente, archivo:línea cuando exista, viewport CSS o ventana efectiva, zoom, orientación, estado, plataforma, pasos, síntoma observado, resultado esperado, norma o heurística, causa confirmada o hipótesis, corrección mínima y prueba de regresión.

No inventes métricas, cobertura, DPI, CLS ni comportamiento de dispositivos. Distingue navegador emulado de dispositivo físico. Agrupa defectos con la misma causa raíz y no los dupliques por viewport.

Lee `references/guia-auditoria.md` cuando necesites la matriz, checks por plataforma, criterios WCAG, severidad o formato de dictamen.

## Formato de salida

Entrega en el chat salvo petición explícita de archivos:

1. **Alcance y método**: fuentes, vistas, entorno, método, dimensiones y exclusiones.
2. **Dictamen**: uno de `CONFORME EN ALCANCE PROBADO`, `CONFORME CON OBSERVACIONES`, `NO CONFORME` o `EVIDENCIA INSUFICIENTE`, con conteo de confirmados, indicios y no evaluados.
3. **Hallazgos priorizados**: `ID | Estado | Severidad | Vista/ubicación | Condición | Impacto | Corrección mínima`.
4. **Correcciones y regresión**: hasta cinco prioridades, con código solo si se sustenta en archivos concretos.
5. **No evaluado** y **Siguiente acción**: la condición ausente y la comprobación de mayor valor.
