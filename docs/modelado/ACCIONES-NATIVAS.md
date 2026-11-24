# Acciones nativas del motor local

Las acciones de este documento procesan archivos locales. No requieren Google
Workspace, MCP ni una conexión de red. La asistencia que consulta DeepInfra es
una función separada y sí requiere conexión; sus propuestas no se ejecutan ni
se aplican automáticamente.

El motor mantiene DuckDB para transformaciones, SQLite para metadatos y Parquet
para resultados de ejecución. No se añade una dependencia de transformación.
Las entradas tabulares admitidas siguen siendo CSV, XLSX y ODS. Las salidas
locales se escriben como archivos nuevos CSV, XLSX u ODS; nunca se sobrescribe
un archivo existente.

## Alinear esquemas

```bash
yachay model align enero.csv febrero.csv --column-maps mapas.json \
  --output combinado.xlsx
```

`mapas.json` contiene un mapa por fuente, en el orden de los argumentos. Los
nombres omitidos se conservan; las columnas ausentes se completan con nulos.
El orden predeterminado es el de primera aparición. Se puede fijar con
`--columns id,fecha,monto`. El append estricto existente no cambia.

```json
[
  {"Fecha": "fecha", "Monto": "monto"},
  {"date": "fecha", "amount": "monto"}
]
```

## Validar filas

```bash
yachay model validate datos.csv calidad.json --errors-output errores.xlsx
```

El informe indica número de fila (la cabecera es la fila 1), regla, campo y
motivo; no copia valores de las celdas al informe. Los tipos de regla son:

```json
[
  {"type": "required", "column": "id"},
  {"type": "unique", "columns": ["id"]},
  {"type": "range", "column": "monto", "min": 0, "max": 1000000},
  {"type": "format", "column": "codigo", "pattern": "^[A-Z]{2}-[0-9]+$"}
]
```

Los campos vacíos se permiten en `range`, `format` y `unique`; usa `required`
para rechazarlos. `format` debe coincidir con todo el valor. El contrato JSON es
un archivo local reutilizable.

## Diagnosticar cruces y deduplicar

`yachay model join` ahora añade tasas de coincidencia, claves repetidas y filas
sin clave al informe del cruce. Las tasas se calculan por filas; una fila con
clave vacía se considera no coincidente.

```bash
yachay model dedupe datos.csv --key id --keep last --output sin_duplicados.csv
```

`--keep` acepta `first` o `last`; conserva el orden original de las filas
seleccionadas. Las filas sin clave se conservan individualmente porque no se
pueden identificar de manera segura como duplicadas.

## Transformar y exportar

```bash
yachay model transform datos.csv calculo.json --output salida.xlsx
```

La especificación admite una operación por invocación:

```json
{"operation": "derive", "name": "total", "expression": "col(\"precio\") * col(\"cantidad\")"}
```

Expresiones disponibles: aritmética, `col("nombre")`, `number`, `text`,
`concat`, `coalesce`, `lower`, `upper`, `trim`, `abs` y `round`. No se admite
SQL libre, acceso a atributos ni ejecución de código.

```json
{
  "operation": "aggregate",
  "by": ["region"],
  "metrics": [
    {"name": "ventas", "function": "sum", "column": "monto"},
    {"name": "registros", "function": "count"}
  ]
}
```

`aggregate` admite `count`, `sum`, `avg`, `min` y `max`. `pivot` acepta
`index`, `pivot`, `value` y una función de agregación; limita el resultado a
100 columnas dinámicas. `sum` y `avg` fallan ante valores no numéricos; `min` y
`max` comparan la representación de origen como texto. `unpivot` acepta `index`
y las `columns` que se convertirán en pares nombre/valor.

```json
{
  "operation": "normalize",
  "numbers": {"monto": {"decimal": ",", "thousands": "."}},
  "dates": {"fecha": "%d/%m/%Y"}
}
```

La normalización solo aplica a columnas declaradas y formatos exactos. Valores
incompatibles se informan por fila; el motor no intenta adivinar formatos.

Las operaciones de alineación, deduplicación y transformación pueden escribir
el resultado a CSV/XLSX/ODS. `--output` debe apuntar a una carpeta existente y a
un nombre que todavía no exista. Las exportaciones tienen límites de filas,
columnas y celdas acordes con los lectores de Yachay.

## Exportar ejecuciones e incorporar deltas

```bash
yachay model export-run 17 resultado.xlsx --root .
yachay model incremental 3 17 delta.csv --key id --root .
```

`export-run` exporta el Parquet guardado en una ejecución. `incremental`
transforma el archivo delta con la misma versión del modelo que produjo la
ejecución base; requiere que esa salida tenga Parquet y metadatos de versión.
Las columnas nuevas se alinean. Para claves repetidas prevalece la fila del
delta; las filas con claves vacías se conservan individualmente. La ejecución
completa sigue disponible con `model exec`.

Cada ejecución nueva registra versión de modelo y motor, nombre y SHA-256 de
las fuentes, parámetros y huella del resultado. No se guardan valores de celdas
en SQLite.
