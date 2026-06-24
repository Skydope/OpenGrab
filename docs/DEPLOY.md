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

### Actualizaciones

- **yt-dlp**: usá el botón "Actualizar motor" en la UI, o `OPENGRAB_AUTOUPDATE=1`.
- **Imagen Docker**: `docker compose pull && docker compose up -d`
- **App de escritorio**: descargá el instalador más reciente desde [Releases](https://github.com/Skydope/OpenGrab/releases/latest)
