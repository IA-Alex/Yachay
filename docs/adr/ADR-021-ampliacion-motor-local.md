# ADR-021 — Ampliación incremental del motor local

- **Estado:** Aceptada
- **Fecha:** 2026-11-24
- **Ámbito:** funciones nativas de modelado y calidad de datos
- **Extiende:** ADR-019 (motor de modelado), ADR-020 (ejecuciones y salidas locales)

## Contexto

El alcance inicial de 13 capacidades (ADR-019) ya ofrece lectura de CSV/XLSX/ODS,
perfilado, entidades y claves, reglas básicas, cruces, ejecuciones versionadas y
diff. El uso de varias fuentes reales requiere además alinear esquemas distintos,
explicar errores por fila, diagnosticar cruces y producir extractos locales
reutilizables. El operador pide ampliar estas funciones desde el motor, sin
integración de Workspace ni ejecución de código generado.

## Decisión

Ampliar Yachay como motor local y determinista, por entregas pequeñas, con estas
capacidades nativas:

1. Alinear esquemas de varias fuentes con orden de columnas estable, mapeo
   explícito de nombres y nulos para columnas ausentes.
2. Validar filas con reglas declaradas (obligatoriedad, unicidad, rangos y
   formatos), devolviendo fila, campo y motivo del error.
3. Diagnosticar cruces: cardinalidad, claves repetidas y tasas de coincidencia.
4. Deduplicar con claves y política de conservación explícitas.
5. Calcular columnas derivadas con expresiones declarativas limitadas, sin SQL
   libre ni código arbitrario.
6. Agregar y pivotar datos con dimensiones, métricas y límites declarados.
7. Exportar resultados, errores y resúmenes como archivos locales nuevos CSV,
   XLSX u ODS, sin modificar la fuente.
8. Interpretar formatos de fecha y número con configuración por fuente.
9. Ejecutar incrementalmente solo cuando una estrategia de clave y cambio haya
   sido declarada; mantener ejecución completa como opción reproducible.
10. Registrar los metadatos necesarios para reproducir una ejecución: versión
    del modelo, huellas de fuentes, parámetros, conteos y versión del motor.

## Orden de implementación

- **Primera entrega:** alineación de esquemas, validación por fila,
  diagnósticos de cruces y deduplicación.
- **Segunda entrega:** expresiones derivadas seguras, agregaciones y pivoteo.
- **Tercera entrega:** exportación local a CSV/XLSX/ODS y formatos de fecha/número.
- **Cuarta entrega:** ejecución incremental y manifiesto de reproducibilidad.

Cada entrega conserva compatibilidad con modelos anteriores; las reglas nuevas
se declaran explícitamente y no alteran resultados existentes por defecto. Los
datos de entrada y salida permanecen en el equipo. Las funciones que consultan
DeepInfra requieren conectividad y se mantienen separadas del procesamiento
local.

## Límites

- No se integra Google Workspace ni MCP en esta ampliación.
- No se sobrescriben archivos de origen; las salidas son archivos nuevos con
  resolución explícita ante colisiones de nombre.
- No se añade SQL libre, ejecución de código, gateway LLM general ni cambios
  automáticos al modelo.
- No se convierte Yachay en plataforma BI: quedan fuera modelo dimensional,
  capa semántica, linaje empresarial y gobernanza.
- El soporte de presentaciones se mantiene fuera del motor tabular; solo se
  considerará como lector auxiliar si existe una necesidad concreta de extraer
  tablas o texto.

## Consecuencias

- DuckDB sigue siendo el único motor de transformación; SQLite conserva solo
  estado y metadatos; Parquet continúa como artefacto interno de ejecución.
- Se amplían los contratos CLI/API y, cuando aporten operación directa, la UI de
  escritorio. Cada cambio de contrato debe reflejarse en OpenAPI y el tracker.
- El trabajo se marca completo por capacidad únicamente cuando su operación y
  límites estén documentados; esta ADR registra el alcance aprobado, no afirma
  que ya esté implementado.

