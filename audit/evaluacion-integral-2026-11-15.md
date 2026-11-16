# Evaluación integral de Yachay

- **Fecha:** 2026-11-15
- **Alcance:** arquitectura, backend/API, almacenamiento, dependencias, seguridad, experiencia de escritorio y operación local.
- **Método:** revisión estática del repositorio, lectura de lockfile/servicios/documentación y consulta de avisos públicos de dependencias.
- **Nivel de validación:** PASSIVE. No se ejecutó la aplicación, la suite de pruebas ni pruebas dinámicas contra el servicio. No se inspeccionó el contenido de `data/`.
- **Cambios al producto:** ninguno. Este documento y `.design/review-report.md` son artefactos de auditoría.

## Dictamen

La base técnica es coherente para una herramienta local: API sobre socket Unix, worker separado, SQLite WAL como autoridad, DuckDB para transformación y Parquet/manifiestos para resultados. El árbol de dependencias está bloqueado con hashes y los servicios systemd aplican restricciones de sistema y límites de recursos. No se encontró PostgreSQL en la implementación, así que RLS, roles y grants de PostgreSQL no aplican.

La mayor brecha observada está entre la bandeja compartida y el worker: el modelado lee fuentes de `inbox/`, mientras el worker toma cualquier archivo de esa misma carpeta y ejecuta un pipeline CSV que mueve el archivo. Esto puede hacer que los flujos de modelado pierdan su fuente cuando API y worker están activos. La otra brecha central es el presupuesto de complejidad de archivos: el límite de 16 MiB del cuerpo HTTP no limita el tamaño expandido ni las filas, columnas o páginas que varios lectores materializan en memoria.

**No se confirmó una vulnerabilidad explotable de forma dinámica.** Los riesgos de ingestión y dependencias abajo se mantienen como SUPPORTED/condicionales hasta verificar su comportamiento en un entorno aislado. No se hizo explotación material.

## Puntuación cualitativa

| Área | Evaluación | Base |
|---|---:|---|
| Arquitectura y separación de responsabilidades | 4/5 | API, worker y almacenamiento tienen responsabilidades explícitas; la bandeja compartida rompe esa separación para fuentes de modelado. |
| Seguridad de API y consultas | 4/5 | Socket Unix con modo 0660, bearer opcional, tamaño de cuerpo acotado, timeout, rutas cerradas y consultas SQL parametrizadas/citas de identificadores. |
| Robustez ante archivos | 2/5 | Se manejan entradas corruptas, pero no hay límites visibles para expansión y complejidad de formatos. |
| Datos y almacenamiento | 3/5 | SQLite WAL, FK y busy timeout están configurados; permisos de datos fuera de systemd dependen del `umask` del proceso. |
| Dependencias y cadena de suministro | 3/5 | Versiones de runtime fijadas y hashes presentes; `odfpy` está sin publicación desde 2020, no hay provenance para su release y `hatchling` no está fijado en `build-system.requires`. |
| Diseño de escritorio | 4/5 | Tokens/componentes compartidos y correcciones visuales recientes; queda una limitación documentada de accesibilidad de tooltips. |
| Operabilidad | 3/5 | Unidades systemd endurecidas y con límites, pero faltan límites de trabajo por archivo y una verificación CI visible en el repositorio. |

## Hallazgos

### INT-01 — El worker y el modelado compiten por los mismos archivos de entrada

- **Prioridad:** P1 — alta para funcionamiento.
- **Estado:** SUPPORTED; confianza STRONGLY INDICATED.
- **Tipo:** conflicto de ciclo de vida / consistencia de operación.
- **Evidencia:** `Worker._scan_inbox` recorre todos los archivos regulares en `inbox/`, los mueve a `processing/` y no filtra por tipo en [worker.py](/home/panda/Yachay/src/motor/worker.py:54). `Worker._process` los entrega a `run_csv_pipeline` en [worker.py](/home/panda/Yachay/src/motor/worker.py:203). A la vez, las rutas de crear modelo, perfilar y ejecutar modelos construyen su ruta bajo `inbox/` en [routes.py](/home/panda/Yachay/src/motor/api/routes.py:365), [routes.py](/home/panda/Yachay/src/motor/api/routes.py:411) y [routes.py](/home/panda/Yachay/src/motor/api/routes.py:468).
- **Impacto:** con ambos servicios activos, el worker puede reclamar una fuente entre la subida y la operación de modelado; las rutas posteriores la reportarán como ausente. Las fuentes no CSV también pueden ir al pipeline CSV y fallar como trabajo de ingesta.
- **Límite de evidencia:** no se ejecutó una secuencia real API+worker ni se midió la carrera.
- **Corrección recomendada:** definir un solo dueño de cada archivo. Separar la bandeja de fuentes de modelado de la cola de ingestión, o cambiar el ciclo de vida para que el worker no retire fuentes que el modelo necesita. Mantener la API como frontera de admisión y documentar claramente la duración/ubicación de cada tipo de fuente.

### INT-02 — Límite HTTP no controla la complejidad expandida del archivo

- **Prioridad:** P1 — alta para robustez; exposición local condicionada por acceso al socket.
- **Estado:** SUPPORTED; capacidad de agotar recursos STRUCTURAL, no ejercida.
- **Tipo:** falta de límites de recursos / CWE-400 potencial.
- **Evidencia:** el servidor limita cuerpos a 16 MiB en [limits.py](/home/panda/Yachay/src/motor/limits.py:12), pero lectores de XLSX materializan todas las filas en una lista en [tables.py](/home/panda/Yachay/src/motor/sources/tables.py:90), ODS expande `numbercolumnsrepeated` en un bucle sin límite visible en [tables.py](/home/panda/Yachay/src/motor/sources/tables.py:128), y PDF acumula todas las líneas extraídas en otra lista en [documents.py](/home/panda/Yachay/src/motor/sources/documents.py:79). Las rutas de perfilado/modelado llaman estos lectores en el proceso API. systemd pone `MemoryMax=2G` para la API y `8G` para el worker, sin límite de filas/páginas/celdas por archivo.
- **Ruta de ataque hipotética:** miembro con permiso de escritura al socket → carga de archivo aceptada → petición de perfil/modelado → expansión/materialización intensiva → posible degradación o caída del servicio. PATH_CONFIDENCE: HYPOTHETICAL; no se probó un archivo de agotamiento.
- **Corrección recomendada:** imponer límites deterministas por formato sobre tamaño expandido, filas/columnas/celdas/páginas y texto extraído; rechazar ODS con repetición que exceda el presupuesto. Mantener el CSV grande en procesamiento por lotes y aislar el lector nativo de PDF solo si el modelo de uso acepta documentos no confiables.

### INT-03 — PyMuPDF está fijado en un rango afectado por un aviso reciente

- **Prioridad:** P2 — revisión de dependencia.
- **Estado:** SUPPORTED para presencia en el artefacto; exploitabilidad no establecida.
- **Tipo:** dependencia nativa con aviso de seguridad.
- **Evidencia:** [pyproject.toml](/home/panda/Yachay/pyproject.toml:15) fija `PyMuPDF==1.26.7`. El aviso CVE-2026-120752 afecta versiones PyMuPDF desde 1.25.3 hasta antes de 2.0 y describe el desbordamiento en `Page.add_freetext_annot()` cuando se pasan cuatro o más puntos de llamada ([SUSE upstream summary](https://www.suse.com/security/cve/CVE-2026-120752.html)). La app sí lee PDFs en [documents.py](/home/panda/Yachay/src/motor/sources/documents.py:79), pero la ruta visible solo abre el PDF y llama `page.get_text()`; no llama a la API de anotaciones citada.
- **Precondiciones:** archivo PDF procesado por las rutas de lectura; pertenencia de la API al grupo autorizado del socket. La triggerability del método vulnerable no aparece en el código inspeccionado.
- **Conclusión:** no afirmar explotación ni exposición de ese método por el mero rango de versión. Planificar actualización compatible y confirmar su ruta de llamada real; conservar el pin si la prueba de compatibilidad no está lista.

### INT-04 — Protección de archivos de datos depende del `umask` al ejecutar fuera de systemd

- **Prioridad:** P2 — confidencialidad local, condicionada al modo del home/directorios.
- **Estado:** SUPPORTED, condicionado; modos efectivos del entorno no medidos.
- **Evidencia:** `MotorPaths.from_root` crea directorios sin modo explícito en [config.py](/home/panda/Yachay/src/motor/config.py:48), SQLite crea la base sin fijar permisos en [db.py](/home/panda/Yachay/src/motor/storage/db.py:11), y los manifiestos usan `Path.write_text` en [publish.py](/home/panda/Yachay/src/motor/publishing/publish.py:36). Las unidades systemd sí definen `UMask=0027` ([motor-api.service](/home/panda/Yachay/docs/systemd/motor-api.service:41), [motor-worker.service](/home/panda/Yachay/docs/systemd/motor-worker.service:47)); los comandos de primer plano documentados no fijan `umask`.
- **Impacto:** si el usuario ejecuta el modo manual con un `umask` permisivo y el árbol de datos/home permite lectura a otros usuarios locales, SQLite, manifiestos o resultados pueden quedar accesibles por permisos heredados. No se inspeccionaron los modos del directorio de datos existente.
- **Corrección recomendada:** fijar una política de permisos al inicializar directorios y archivos en modo manual, o documentar y aplicar un `umask` privado como requisito de operación. Revisar permisos de instalaciones existentes sin cambiar datos automáticamente.

### INT-05 — ODFPy tiene una historia de publicación larga y provenance ausente

- **Prioridad:** P2/P3 — sostenibilidad de dependencia y distribución.
- **Estado:** CONFIRMED para metadatos; sin evidencia de malware ni CVE relevante confirmada.
- **Evidencia:** `odfpy==1.4.1` está fijada en [pyproject.toml](/home/panda/Yachay/pyproject.toml:12). PyPI registra la publicación como 18-ene-2020 y sin provenance para sus archivos ([PyPI odfpy](https://pypi.org/project/odfpy/)). El lockfile sí fija hashes del artefacto. PyPI declara licencias Apache, GPL y LGPL para el paquete.
- **Impacto:** mayor incertidumbre sobre mantenimiento y procedencia; revisar obligaciones de licencia antes de distribuir el producto. Esto no demuestra que el paquete sea inseguro o abandonado.
- **Corrección recomendada:** confirmar que el formato ODS sigue siendo necesario y mantener una revisión de seguridad/procedencia antes de actualizar o distribuir; no sustituirlo sin una incompatibilidad o riesgo demostrado.

### INT-06 — El lock de aplicación no fija el backend de build y no hay CI visible

- **Prioridad:** P2 — reproducibilidad/operación de entrega.
- **Estado:** CONFIRMED estructuralmente en los archivos inspeccionados.
- **Evidencia:** `uv.lock` resuelve 13 paquetes runtime/dev y `uv lock --check` pasó. Sin embargo, [pyproject.toml](/home/panda/Yachay/pyproject.toml:29) declara `hatchling` sin versión; el árbol de `uv tree --locked` no lo incluye. La búsqueda del repositorio no encontró workflow de CI bajo `.github/`.
- **Impacto:** la resolución de instalación de aplicación es reproducible, pero la dependencia de build puede variar al crear paquetes; el gate local [scripts/test.sh](/home/panda/Yachay/scripts/test.sh) no aparece conectado a CI en este checkout.
- **Corrección recomendada:** fijar el backend de build en el mecanismo de build elegido y automatizar el gate existente al cambiar código/dependencias. No añadir un pipeline de publicación hasta que exista un destino de distribución.

### INT-07 — Un registro de seguimiento de dependencias contradice el manifiesto actual

- **Prioridad:** P3 — calidad documental.
- **Estado:** CONFIRMED en archivos actuales.
- **Evidencia:** [seguimiento-local.md](/home/panda/Yachay/docs/seguimiento-local.md:632) afirma que `pyproject.toml` conserva CrewAI; el manifiesto actual lista DuckDB, odfpy, openpyxl, PyArrow, PyMuPDF y python-docx, sin CrewAI. Esto complica distinguir dependencias actuales de históricas.
- **Corrección recomendada:** marcar la referencia como histórica o actualizarla para que describa el manifiesto vigente.

## Controles favorables observados

- Socket Unix, sin listener TCP, modo de socket 0660, timeout de lectura y límite duro del cuerpo HTTP en [server.py](/home/panda/Yachay/src/motor/api/server.py:34) y [server.py](/home/panda/Yachay/src/motor/api/server.py:143).
- Bearer opcional con comparación de tiempo constante en [server.py](/home/panda/Yachay/src/motor/api/server.py:107); cuando no hay token, el límite de identidad es el permiso del socket/grupo local.
- Nombres de archivo comprobados antes de crear rutas en [routes.py](/home/panda/Yachay/src/motor/api/routes.py:250); SQL de modelo cita identificadores y parametriza valores en [engine.py](/home/panda/Yachay/src/motor/model/engine.py:21).
- SQLite activa WAL, claves foráneas y `busy_timeout` en [db.py](/home/panda/Yachay/src/motor/storage/db.py:11).
- Las unidades systemd aplican `NoNewPrivileges`, `ProtectSystem=strict`, familias de socket restringidas, `CapabilityBoundingSet=` vacío y límites de recursos. Son controles de defensa en profundidad, no aislamiento de cada documento.
- `uv tree --locked --depth 3` mostró 13 paquetes; `uv lock --check` pasó. El lockfile incluye hashes para los artefactos resueltos.
- El aviso de PyArrow CVE-2026-25087 cubre `>=15.0.0,<23.0.1`; la versión fijada `26.0.1` queda fuera del rango publicado. El advisory también explica que la API Python no expone la función C++ de prebuffer implicada ([GitHub Advisory Database](https://github.com/advisories/GHSA-rgxp-2hwp-jwgg)).
- El informe de escritorio del 12-nov registra FE-001 a FE-010 como corregidos y deja abierto únicamente FE-011, tooltips accesibles; se referencia sin repetir esas correcciones en [audit/hallazgos.md](/home/panda/Yachay/audit/hallazgos.md:142).

## Ruta original de mejora (baseline pre-reparación)

La lista siguiente registra la recomendación emitida antes de aplicar la remediación; su estado actual se actualiza en el cierre final.

1. Corregir el ciclo de vida/propiedad de archivos de INT-01; preservar los flujos actuales de carga y modelado.
2. Añadir límites de complejidad de archivo y carga de INT-02; no subir los límites globales de recursos sin medición.
3. Fijar el manejo de permisos del modo manual de INT-04 y resolver la compatibilidad de PyMuPDF antes de actualizar.
4. Cerrar reproducibilidad de build y discrepancias de documentación (INT-06, INT-07); dar seguimiento a ODFPy como riesgo de mantenimiento/licencia (INT-05).
5. Atender UI-01 del [informe de diseño](../.design/review-report.md) y revalidar teclado/zoom con ejecución real.

## Validaciones originalmente pendientes (baseline pre-reparación)

La lista siguiente describe verificaciones pendientes al emitir el diagnóstico; se completaron durante la remediación donde se indica en el cierre.

- Reproducir el orden de carga → perfil/modelo con API y worker activos en un root temporal.
- Probar límites de filas/celdas/páginas con datos sintéticos no dañinos, después de fijar presupuesto seguro y en entorno aislado.
- Ejecutar la suite existente y comprobar el gate de lint/cobertura en la revisión de implementación, no durante este informe estático.
- Inspeccionar permisos efectivos en una instalación de prueba y validar una rueda construida con dependencias de build fijadas.
- Confirmar la compatibilidad entre las rutas del lector PDF usadas por la app y el parche correspondiente de PyMuPDF.

## Seguimiento de remediación — 2026-11-15

Se implementaron INT-01, INT-02, INT-04, INT-06 y INT-07; UI-01 quedó retirado
porque el helper afectado no tenía llamadas en ninguna vista. INT-03 se cerró
mediante revisión de alcanzabilidad: el código no llama a la API de anotaciones
citada y se conserva el pin para evitar incorporar varias dependencias que la
aplicación no utiliza. INT-05 permanece como decisión explícita: conservar el
lector ODS requerido, con revisión de licencia/provenance antes de distribución.

El baseline previo a cambios fue `./scripts/test.sh`: 477 pruebas, 7 omitidas,
100 % de cobertura y Ruff limpio. Los resultados finales: `./scripts/test.sh` ejecutado con 493 pruebas, 7 omitidas,
100 % de cobertura y Ruff limpio; `uv lock --check`, `uv build` y `pip-audit --local`
completaron sin hallazgos conocidos. Se verificó la ruta de carga → worker → modelo
mediante pruebas de integración con filesystem temporal. No se inició una sesión
interactiva de escritorio ni se volvió a validar lector de pantalla/zoom. El árbol
actual bloqueado contiene 13 paquetes y no incluye CrewAI/Chromadb. PyMuPDF se
mantiene fijado: no se llamó a la API señalada por el advisory revisado; ODFPy
queda como revisión de provenance/licencia antes de una distribución externa.

El build y las pruebas no miden consumo/performance con documentos máximos ni
validan una instalación empaquetada en cada sistema operativo soportado.
