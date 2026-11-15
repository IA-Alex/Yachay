"""Constantes compartidas por la interfaz de escritorio."""

REFRESH_MS = 5000
DEFAULT_LIMIT = 100
# Límite real de cuerpo de la API (motor/limits.py: MAX_REQUEST_BYTES). Se
# duplica aquí para validar ANTES de enviar, sin importar el paquete `motor`
# (la interfaz es un cliente autónomo).
MAX_REQUEST_BYTES = 16 * 1024 * 1024

# Catálogo visible de capacidades. Es deliberadamente estático: el panel
# explica el alcance documentado sin convertirlo en una métrica inventada ni
# afirmar que una vista ofrece una operación que solo existe en CLI/API.
FUNCTION_CATALOG = (
    ("M1", "Perfilado", "Columnas, tipos, nulos y duplicados", "Cargar archivo"),
    ("M2", "Modelo", "Entidades, atributos y claves", "Modelo"),
    ("M3", "Transformación", "Shape, merge, estándares y dominios", "CLI / API"),
    ("M4", "Cruces", "Joins inner, left y anti; huérfanos", "CLI / API"),
    ("M5", "Repetición y diff", "Ejecuciones con huella y comparación", "Ejecutar / Resultados"),
    ("I", "Ingesta", "Admisión, cola, leases y worker", "Worker / Cola"),
    ("P", "Publicación", "Datasets versionados y manifest", "Catálogo"),
)
