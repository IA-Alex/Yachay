# Revisión de diseño — Yachay Desktop

- **Modo:** review (solo informe)
- **Fecha:** 2026-11-15
- **Alcance:** `desktop/yachay_desktop/`, experiencia de escritorio tkinter
- **Base de evidencia:** inspección estática actual y `audit/hallazgos.md`, auditoría con capturas del 2026-11-12.
- **Cambios de producto:** ninguno.

## Primera impresión

Yachay comunica una herramienta de trabajo local con una interfaz operativa sobria. La navegación cubre estado, archivos, modelos, ejecución, resultados, trabajos y datasets. Los tokens compartidos para color, tipografía, espaciado y foco ayudan a que las vistas parezcan partes de una aplicación coherente. El lenguaje y los artefactos concretos (archivos, modelos y ejecuciones) refuerzan la credibilidad.

## Evaluación

| Área | Puntuación | Evidencia y límite |
|---|---:|---|
| Intencionalidad | 4/5 | El producto prioriza operaciones de modelado y seguimiento; `theme.py` centraliza roles visuales. |
| Legibilidad | 4/5 | La revisión visual del 12-nov corrigió símbolos, alineación y jerarquía de estados; no se hicieron capturas nuevas en esta auditoría. |
| Usabilidad | 4/5 | Las siete vistas y los estados vacíos revisados cubren las tareas principales; no se recorrió la app en vivo hoy. |
| Reflujo | 3/5 | Se corrigió el desbordamiento horizontal en 880 px. No hay evidencia reciente para zoom al 200 %, tamaños adicionales o escalado del sistema. |
| Accesibilidad | 3/5 | Hay token de foco y los estados no dependen solo del color; la auditoría anterior deja abierto el acceso de tooltips a teclado y lectores de pantalla. |
| Sensación de rendimiento | 3/5 | El cliente usa socket local con timeout; este informe no midió latencia ni bloqueo visual durante operaciones largas. |
| Credibilidad del producto | 4/5 | La UI presenta modelos, archivos y resultados reales; el alcance documentado evita paneles decorativos y funciones no previstas. |

Las puntuaciones describen evidencia disponible; no equivalen a certificación de accesibilidad ni a verificación runtime completa.

## Recorrido de tarea

El flujo documentado va desde conectar la instancia local y seleccionar archivos hasta perfilar datos, crear un modelo, ejecutar reglas y revisar resultados. La interfaz mantiene contexto de estado y resultados mediante vistas separadas. La auditoría previa probó las vistas a 1080×680 y 880×560, y corrigió las colisiones del indicador de actualización, el estado vacío de ejecución y el scrollbar horizontal persistente.

En este turno no se inició la app ni se recorrió el flujo con teclado o lector de pantalla. Los defectos visuales marcados como corregidos se toman de evidencia runtime del 12-nov, no de una reproducción nueva.

## Sistema y consistencia

- La paleta, tipografía, espaciado y estados usan tokens compartidos en `desktop/yachay_desktop/theme.py`.
- El estado se expresa con texto y símbolo además del color; existe una alternativa ASCII cuando la fuente no resuelve glifos.
- El helper de tablas unifica encabezados, columna elástica y scrollbar; esto resuelve varios defectos comunes en un solo punto.
- La interfaz usa componentes habituales de tkinter/ttk. No se detecta una necesidad de rediseño ni de añadir dependencias visuales.

## Hallazgos

| ID | Severidad | Área | Ubicación / evidencia | Comportamiento actual | Corrección propuesta | Impacto |
|---|---|---|---|---|---|---|
| UI-01 | Baja | Accesibilidad | `desktop/yachay_desktop/widgets.py:401`; FE-011 en `audit/hallazgos.md` | Los tooltips aparecen por interacción de ratón y no tienen una vía documentada para teclado o lectores de pantalla. | Mantener la explicación esencial visible o asociarla al nombre/ayuda accesible del control; comprobar qué soporte de accesibilidad ofrece tkinter en los sistemas objetivo antes de añadir una dependencia. | Algunas personas no reciben la información secundaria del control. |

## Recomendaciones priorizadas

1. Resolver UI-01 con el mecanismo accesible más simple que soporte tkinter; evitar introducir `pyatspi` hasta verificar que sea necesario en los entornos objetivo.
2. En una siguiente comprobación, recorrer con teclado completo el flujo de carga → modelo → ejecución → resultado, comprobar nombres/estados de controles con lector de pantalla y verificar zoom/reflujo. Priorizar estas comprobaciones antes de cambios cosméticos.

No recomiendo cambiar la paleta, rehacer la composición ni añadir vistas: la evidencia disponible respalda el sistema existente y el producto mantiene un alcance funcional deliberadamente acotado.

## Verificación y veredicto pre-remediación

Este veredicto refleja el diagnóstico previo al cierre UI-01, no el estado posterior. En el cierre se retiró el helper sin llamadas; las pruebas actuales cubren la suite automatizada, pero no sustituyen la comprobación interactiva de teclado, lector de pantalla y zoom al 200 %.

## Cierre UI-01 — 2026-11-15

El helper de tooltip era el único productor y no tenía llamadas en las vistas
actuales. Se eliminó el argumento y el helper no usado; no había instrucciones
visibles de controles que quedaran inaccesibles al retirarlo. No se añadieron
dependencias ni nuevos elementos visuales. La interacción actual de la UI debe
revalidarse en runtime junto con el recorrido de teclado completo.
