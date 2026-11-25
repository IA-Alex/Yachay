# systemd — Yachay

Unidades de usuario para el motor de datos local. systemd solo **supervisa los
procesos**: nunca es autoridad de datos. El estado vive en SQLite y los
manifests (ADR-004/005); el worker es la única autoridad de ejecución (ADR-008)
y la API una proyección de solo lectura (ADR-016).

## Unidades

| Unidad | Proceso | Rol |
| --- | --- | --- |
| `motor-worker.service` | `motor run` | Escanea, reclama, reconcilia, publica y recolecta |
| `motor-api.service` | `motor api` | Sirve la API v1 en un socket Unix |

## Instalar (usuario)

```bash
mkdir -p ~/.config/systemd/user
cp docs/systemd/motor-worker.service ~/.config/systemd/user/
cp docs/systemd/motor-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now motor-worker.service motor-api.service
```

## Adaptador DeepInfra con firewall por UID

El adaptador es una **unidad de sistema** con UID exclusivo `yachay-net`. No
abre puertos entrantes ni resuelve DNS desde su proceso. nftables permite solo
TCP 443 hacia las IP públicas que el refrescador de DNS guarda para
`api.deepinfra.com`; rechaza el resto de la salida de ese UID. Las IP caducan a
los 180 segundos y se refrescan cada 60; si el refresco falla, la asistencia
falla cerrada. La API y el worker conservan su aislamiento actual.

No instales esta regla sobre un firewall activo de UFW/firewalld ni sobre un
servicio nftables que recargue todo el ruleset sin integrarla primero. El diseño
añade una tabla propia y no ejecuta `flush ruleset`, pero la recarga de otro
administrador podría borrar esa tabla.

```bash
# Crear el secreto como panda; no pegarlo en el chat ni en comandos con historial.
install -d -m 700 ~/.config/yachay
install -m 600 /dev/null ~/.config/yachay/deepinfra-api-key
# Guardar solo el token en ese archivo.

# Como root, una vez confirmado que no hay otro gestor de firewall activo:
apt install nftables
install -D -m 644 docs/systemd/yachay-deepinfra.sysusers /etc/sysusers.d/yachay-deepinfra.conf
systemd-sysusers /etc/sysusers.d/yachay-deepinfra.conf
install -D -m 644 docs/systemd/yachay-deepinfra.nft /etc/yachay-deepinfra.nft
install -D -m 644 scripts/deepinfra_firewall_refresh.py /usr/local/libexec/yachay-deepinfra-firewall-refresh
install -D -m 644 docs/systemd/yachay-deepinfra-firewall.service /etc/systemd/system/yachay-deepinfra-firewall.service
install -D -m 644 docs/systemd/yachay-deepinfra-firewall.timer /etc/systemd/system/yachay-deepinfra-firewall.timer
install -D -m 644 docs/systemd/motor-deepinfra.service /etc/systemd/system/motor-deepinfra.service
systemctl daemon-reload
systemctl enable --now yachay-deepinfra-firewall.timer motor-deepinfra.service

# Como panda, publicar el socket usado por la API.
install -D -m 644 docs/systemd/motor-api.service ~/.config/systemd/user/motor-api.service
systemctl --user daemon-reload
systemctl --user restart motor-api.service
```

Las rutas `/home/panda/Yachay` y `/home/panda/.config/...` de la unidad de
sistema deben ajustarse si el usuario o la ubicación del checkout son distintos.
La tabla nftables limita UID, IP y puerto; la validación TLS y el host HTTP fijo
siguen siendo necesarios porque las IP de proveedor pueden ser compartidas.

## Verificar

```bash
systemctl --user status motor-worker motor-api
journalctl -u motor-deepinfra -f
journalctl -u yachay-deepinfra-firewall -f
journalctl --user -u motor-worker -f          # log JSON, una línea por ciclo/job
ls -l "$XDG_RUNTIME_DIR/motor/motor.sock"     # srw-rw---- (modo 660)
curl --unix-socket "$XDG_RUNTIME_DIR/motor/motor.sock" http://localhost/v1/health
```

## Operar

```bash
systemctl --user restart motor-worker
systemctl --user stop motor-api        # el socket se elimina al parar
systemctl --user disable --now motor-worker motor-api
```

## Sin systemd (modo degradado)

Las unidades son un **perfil operativo**, no un requisito. El motor funciona
igual en primer plano:

```bash
PYTHONPATH=src .venv/bin/python -m motor run --root .      # worker
PYTHONPATH=src .venv/bin/python -m motor api --root .      # API
```

## Decisiones y límites

- **`Type=exec`, sin `WatchdogSec`.** El pipeline es de un solo disparo y no hay
  fronteras de lote donde latir; un watchdog mataría CSVs lentamente legítimos y,
  con `MAX_ATTEMPTS=3`, los volvería fallos deterministas. Cablearlo es **T-013**.
- **Identidad separada para red.** API y worker son unidades de usuario. El
  adaptador corre como `yachay-net` en una unidad de sistema para que nftables
  pueda aplicar una política de salida por UID. Su instalación necesita root.
- **Presupuestos de recursos** (`MemoryMax`, `CPUQuota`, …) son del **perfil
  NORMAL, valores de arranque, NO calibrados** (spec sección 16).
- **Red acotada por UID.** API y worker no tienen red IP; el API elimina la
  credencial del proveedor de su entorno. systemd la monta como credencial
  efímera solo al adaptador. nftables permite TCP/443 a conjuntos de IP DNS con
  caducidad y rechaza el resto de la salida de `yachay-net`. El adaptador no
  escucha TCP, usa AF_UNIX para solicitudes y la lista de IP actualizada para
  HTTPS; su vista de archivos solo expone `src/` y `.venv/` en modo lectura.

Ver `docs/adr/ADR-018.md` para el detalle de las decisiones.
