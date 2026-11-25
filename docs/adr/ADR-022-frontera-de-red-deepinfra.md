# ADR-022: frontera de red para DeepInfra

- **Estado:** aceptada e implementada.
- **Fecha:** 2026-11-24.
- **Contexto:** el servicio `motor-api` debe conservar su aislamiento de red,
  pero la asistencia de modelado depende de DeepInfra. Habilitar red al API
  expondría todas sus rutas y su código al mismo permiso de salida.
- **Decisión:** mover la llamada remota a `motor-deepinfra`, un proceso separado
  con UID de sistema estático `yachay-net`, que ofrece únicamente propuestas
  sobre un socket Unix local con grupo `panda`.
  La clave se carga desde `~/.config/yachay/deepinfra-api-key` mediante
  `LoadCredential`; API y worker la eliminan del entorno. El adaptador solo ve
  temporalmente esa credencial, `src/` y `.venv/` en modo de solo lectura. No publica un puerto
  TCP y solo usa el host y ruta HTTPS fijados en código. Un refrescador de sistema
  resuelve DNS como root, valida IP públicas, actualiza un conjunto nftables y
  publica una lista con caducidad de 180 segundos. nftables permite al UID
  `yachay-net` solo TCP 443 a esas IP y rechaza el resto de su salida. El
  adaptador no resuelve DNS por sí mismo. TLS valida certificado y hostname, no
  sigue redirecciones y limita tiempo y tamaño de respuesta. La entrada local limita
  tamaño y concurrencia. El perfil enviado permite solo nombres de columna,
  conteos, tipo inferido y rango numérico; no permite filas ni campos libres.
- **Flujo:** API lee y perfila la fuente local → manda instrucción y perfil por
  AF_UNIX → adaptador llama a DeepInfra → API vuelve a validar la propuesta y
  presenta vista previa → solo una aceptación explícita crea nueva versión.
- **Límite de despliegue:** las IP de CDN pueden ser compartidas. nftables
  limita UID, IP y puerto, mientras el código fija Host/SNI y valida TLS. Un
  proxy de salida con allowlist SNI sería necesario para limitar el hostname
  incluso ante ejecución arbitraria dentro del adaptador. La unidad de sistema
  requiere root y nftables; no debe mezclarse sin integración con UFW,
  firewalld ni otra unidad que vacíe el ruleset.
- **Consecuencias:** la API y el worker mantienen su perfil sin red; la
  disponibilidad de asistencia depende del adaptador, la clave y DeepInfra. El
  modelo no ejecuta reglas ni recibe filas fuente. No se añade un gateway LLM
  genérico ni un servidor MCP.
