# Production Deployment

## Reverse Proxy with Nginx + TLS

See the `nginx/` directory for the recommended configuration (`opengrab.conf`). It handles:

- HTTP → HTTPS redirect
- TLS termination (point `ssl_certificate` and `ssl_certificate_key` to your cert)
- SSE-friendly settings (`proxy_buffering off`, 3600s timeouts)
- Security headers (HSTS, X-Frame-Options, X-Content-Type-Options)
- Docker DNS resolver so nginx starts even if OpenGrab is temporarily down

Drop it into your nginx `conf.d/` directory and reload.

## Systemd Service (bare-metal Linux)

An example unit file is provided at [`docs/examples/opengrab.service`](examples/opengrab.service). Copy it to `/etc/systemd/system/`, adjust paths, and enable:

```bash
sudo cp docs/examples/opengrab.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opengrab
```

## Docker Compose (advanced)

Recommendations for production:

- Persistent volumes for `/downloads` and the database
- Healthcheck with `restart: unless-stopped`
- Set `OPENGRAB_TOKEN` for authentication
- Consider `OPENGRAB_TRUST_XFF=1` when behind a reverse proxy
- Mount `./downloads:/downloads` for persistent storage
- See [`docker-compose.yml`](../docker-compose.yml) for the full annotated config

## Backup and Recovery

### Online backup (recommended)

SQLite's `.backup` command takes a consistent snapshot without stopping the service:

```bash
# Docker
docker exec opengrab sqlite3 /downloads/opengrab.db ".backup /downloads/opengrab_backup.db"

# Bare-metal (from the downloads directory)
sqlite3 opengrab.db ".backup opengrab_backup.db"
```

### Automated backup script

[`scripts/backup.sh`](../scripts/backup.sh) handles backup + retention:

```bash
# Backup the DB only
./scripts/backup.sh /path/to/downloads

# Backup DB + downloads directory
./scripts/backup.sh /path/to/downloads --include-downloads
```

The script keeps the last 7 daily backups (files named `opengrab_YYYYMMDD.db`).
Older backups are pruned automatically.

### Restore

1. Stop OpenGrab: `docker compose stop` (or `systemctl stop opengrab`)
2. Replace the DB file:
   ```bash
   cp opengrab_20260701.db /downloads/opengrab.db
   ```
3. Start the service. `_migrate()` on startup handles any schema changes
   automatically — the restored DB will be brought forward to the current
   schema version.
4. Verify: check `GET /api/metrics` — jobs_done should reflect the restored
   history.

**Important**: restoring an older DB to a newer code version is safe (forward
migration). Downgrading code after restoring a newer DB is **not supported** —
if you need to roll back, restore from a backup made with that older version.

### Recovery after disk crash

If the DB file is lost or corrupted:

1. Stop OpenGrab
2. Replace with the latest backup
3. Incomplete downloads (workdirs from running jobs at crash time) are
   cleaned automatically by `reconcile_startup()` at next launch — no
   manual cleanup needed

## Updates

- **yt-dlp**: use the "Update engine" button in the web UI, or set `OPENGRAB_AUTOUPDATE=1` for auto-update on container start. Pinned by default for reproducible builds — Dependabot keeps the pin current.
- **Docker image**: `docker compose pull && docker compose up -d`
- **Desktop app**: download the latest installer from [Releases](https://github.com/Skydope/OpenGrab/releases/latest)

---

## Español

### Reverse Proxy con Nginx + TLS

La carpeta `nginx/` contiene `opengrab.conf` con la configuración recomendada. Copiala a `conf.d/` de tu nginx y recargá.

### Servicio Systemd (bare-metal Linux)

Hay un unit file de ejemplo en [`docs/examples/opengrab.service`](examples/opengrab.service). Copialo a `/etc/systemd/system/`, ajustá las rutas y habilitá:

```bash
sudo cp docs/examples/opengrab.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opengrab
```

### Docker Compose (avanzado)

Recomendaciones para producción:

- Volúmenes persistentes para `/downloads` y la base de datos
- Healthcheck con `restart: unless-stopped`
- Configurá `OPENGRAB_TOKEN` para autenticación
- `OPENGRAB_TRUST_XFF=1` cuando esté detrás de un reverse proxy
- Montá `./downloads:/downloads` para almacenamiento persistente

### Backup y Recuperación

#### Backup online (recomendado)

El comando `.backup` de SQLite toma una snapshot consistente sin detener el servicio:

```bash
# Docker
docker exec opengrab sqlite3 /downloads/opengrab.db ".backup /downloads/opengrab_backup.db"

# Bare-metal (desde el directorio de downloads)
sqlite3 opengrab.db ".backup opengrab_backup.db"
```

#### Script automatizado

[`scripts/backup.sh`](../scripts/backup.sh) maneja backup + retención:

```bash
# Backup solo de la DB
./scripts/backup.sh /ruta/a/downloads

# Backup de DB + directorio de descargas
./scripts/backup.sh /ruta/a/downloads --include-downloads
```

El script guarda los últimos 7 backups diarios (`opengrab_AAAAMMDD.db`). Los
más viejos se borran automáticamente.

#### Restauración

1. Detené OpenGrab: `docker compose stop` (o `systemctl stop opengrab`)
2. Reemplazá el archivo de DB:
   ```bash
   cp opengrab_20260701.db /downloads/opengrab.db
   ```
3. Iniciá el servicio. `_migrate()` en el arranque aplica cambios de schema
   automáticamente.
4. Verificá: `GET /api/metrics` — `jobs_done` debería reflejar el historial
   restaurado.

**Importante**: restaurar una DB vieja en una versión de código más nueva es
seguro (forward migration). Restaurar una DB más nueva con código más viejo
**no está soportado**.

#### Recuperación tras crash de disco

Si el archivo de DB se pierde o corrompe:

1. Detené OpenGrab
2. Reemplazá con el último backup
3. Las descargas incompletas (workdirs de jobs corriendo al momento del crash)
   se limpian automáticamente por `reconcile_startup()` en el próximo arranque

### Actualizaciones

- **yt-dlp**: usá el botón "Actualizar motor" en la UI, o `OPENGRAB_AUTOUPDATE=1`.
- **Imagen Docker**: `docker compose pull && docker compose up -d`
- **App de escritorio**: descargá el instalador más reciente desde [Releases](https://github.com/Skydope/OpenGrab/releases/latest)
