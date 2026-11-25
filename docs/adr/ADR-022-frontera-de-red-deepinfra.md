# ADR-022: frontera de red para DeepInfra

- **Estado:** aceptada e implementada.
- **Fecha:** 2026-11-24.
- **Contexto:** el servicio `motor-api` debe conservar su aislamiento de red,
  pero la asistencia de modelado depende de DeepInfra. Habilitar red al API
  expondría todas sus rutas y su código al mismo permiso de salida.
- **Decisión:** mover la llamada remota a `motor-deepinfra`, un proceso separado
  que ofrece únicamente generación de propuestas sobre un socket Unix local.
  La clave se carga desde `~/.config/yachay/deepinfra-api-key` mediante
  `LoadCredential`; API y worker la eliminan del entorno. El adaptador solo ve
  temporalmente esa credencial, `src/` y `.venv/` en modo de solo lectura. No publica un puerto
  TCP y solo usa el host y ruta HTTPS fijados en código. DNS debe resolver a
  direcciones globales; la conexión TLS valida certificado y hostname, no sigue
  redirecciones y limita tiempo y tamaño de respuesta. La entrada local limita
  tamaño y concurrencia. El perfil enviado permite solo nombres de columna,
  conteos, tipo inferido y rango numérico; no permite filas ni campos libres.
- **Flujo:** API lee y perfila la fuente local → manda instrucción y perfil por
  AF_UNIX → adaptador llama a DeepInfra → API vuelve a validar la propuesta y
  presenta vista previa → solo una aceptación explícita crea nueva versión.
- **Límite de despliegue:** systemd restringe qué proceso puede abrir sockets IP,
  pero no aplica una allowlist L3/L4 del hostname. La aplicación fija el destino
  y valida DNS/TLS; instalaciones que exijan control de salida de red frente a
  compromiso del proceso deben añadir una regla de firewall o proxy egress que
  permita `api.deepinfra.com:443`.
- **Consecuencias:** la API y el worker mantienen su perfil sin red; la
  disponibilidad de asistencia depende del adaptador, la clave y DeepInfra. El
  modelo no ejecuta reglas ni recibe filas fuente. No se añade un gateway LLM
  genérico ni un servidor MCP.
