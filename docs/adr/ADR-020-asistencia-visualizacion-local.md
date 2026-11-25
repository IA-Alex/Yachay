# ADR-020: propuestas de modelado y visualización local

- Estado: aceptada e implementada.
- Frontera de red y secreto del proveedor: ver ADR-022.
- Contexto: herramienta de escritorio personal; se quiere crear visualizaciones sin
  costo de infraestructura y facilitar transformaciones sin ampliar el motor con
  funciones generadas dinámicamente.
- Decisión:
  - Persistir cada salida de ejecución del modelado como Parquet en
    `data/model-outputs/`, además de su huella SQLite. Añadir metadatos mediante una
    migración aditiva de esquema v4.
  - Generar reportes HTML estáticos localmente, con SVG y tabla accesible. No usar
    CDN, servicio hospedado ni dependencia de charts.
  - Ofrecer una vista de propuestas opcional, respaldada por DeepInfra con
    `DEEPINFRA_API_KEY`. El proveedor recibe instrucción, columnas y estadísticas
    de perfil; nunca las filas del archivo.
  - Limitar la respuesta a reglas registradas (`rename`, `cast`, `map`, `domain`,
    `filter`). Validar de nuevo en servidor, previsualizar, y requerir aceptación
    explícita para crear una versión que copia entidades, atributos, claves,
    reglas y cruces. No ejecutar automáticamente.
  - Confirmar antes de regenerar o borrar un informe. El borrado elimina solo HTML,
    no el resultado Parquet.
- Consecuencias: informes locales sin costo de infraestructura. El uso del modelo
  puede tener costo según la tarifa vigente del proveedor. Las ejecuciones nuevas
  ocupan espacio local adicional y quedan sujetas al borrado explícito de datos.
- Alternativas descartadas: JavaScript de CDN, dashboard alojado, código/SQL generado
  por IA y ejecución automática de propuestas.
