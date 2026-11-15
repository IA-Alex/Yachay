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

## Verificar

```bash
systemctl --user status motor-worker motor-api
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
- **Sin red.** El worker y la API solo usan AF_UNIX (la API añade
  `IPAddressDeny=any`).

Ver `docs/adr/ADR-018.md` para el detalle de las decisiones.
