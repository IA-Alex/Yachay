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
| `motor-deepinfra.service` | `motor deepinfra` | Adaptador de salida limitado a propuestas de DeepInfra |

## Instalar (usuario)

```bash
mkdir -p ~/.config/systemd/user
cp docs/systemd/motor-worker.service ~/.config/systemd/user/
cp docs/systemd/motor-api.service ~/.config/systemd/user/
cp docs/systemd/motor-deepinfra.service ~/.config/systemd/user/
install -d -m 700 ~/.config/yachay
install -m 600 /dev/null ~/.config/yachay/deepinfra-api-key
# Guardar solo el token en ~/.config/yachay/deepinfra-api-key (modo 600)
systemctl --user daemon-reload
systemctl --user enable --now motor-worker.service motor-deepinfra.service motor-api.service
```

## Verificar

```bash
systemctl --user status motor-worker motor-api
journalctl --user -u motor-deepinfra -f
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
- **Sin usuario `motor` ni `/opt`.** Las unidades son de usuario y corren como el
  operador. Una variante de sistema con usuario dedicado requiere root y queda
  documentada, no ejecutada.
- **Presupuestos de recursos** (`MemoryMax`, `CPUQuota`, …) son del **perfil
  NORMAL, valores de arranque, NO calibrados** (spec sección 16).
- **Red acotada por proceso.** Worker y API no tienen red IP; el API elimina
  la credencial del proveedor de su entorno. systemd la monta como credencial
  efímera solo para `motor-deepinfra`. Solo ese proceso puede abrir salida IP,
  no escucha TCP, recibe solicitudes por socket Unix y su código fija el destino
  HTTPS a `api.deepinfra.com`. Su vista de archivos expone `src/` y `.venv/`,
  ambos de solo lectura. El filtro por hostname se aplica en la
  aplicación; un firewall/egress proxy del host sigue siendo necesario si se
  requiere una restricción de destino también a nivel de red.

Ver `docs/adr/ADR-018.md` para el detalle de las decisiones.
