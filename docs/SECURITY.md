# Security Policy

## SSRF Defense (Defense in Depth)

OpenGrab acts as a proxy — yt-dlp makes HTTP requests from your server to arbitrary URLs. A two-layer defense prevents Server-Side Request Forgery.

### Layer 1 — Application-level validation (`download.py`)

The `_is_safe_url()` gate runs before every URL reaches yt-dlp:

1. **Scheme lock**: only `http` and `https` (blocks `file://`, `ftp://`, `javascript:`, `data:`)
2. **Hostname blocklist**: `localhost`, `0.0.0.0`, `::1`, `.local`, `.localhost`
3. **IP literal check**: blocks private, loopback, link-local (`169.254.0.0/16` — includes cloud metadata), reserved, multicast, and unspecified ranges
4. **DNS resolution**: resolves ALL IPs (A + AAAA records) via `getaddrinfo` and validates each. Closes the classic bypass where `evil.com` resolves to `10.0.0.5`
5. **Strict on failure**: if DNS fails, the request is blocked — a transient false negative is safer than a silent bypass. ULA IPv6 (`fc00::/7`) and IPv4-mapped IPv6 (`::ffff:10.0.0.5`) are covered by `is_private` (CPython 3.12+)

### Layer 2 — Host-level egress firewall (`scripts/egress-lockdown.sh`)

Closes the TOCTOU gap between Layer 1's DNS resolution and yt-dlp's actual connection:

- Inserts DROP rules in the host's `DOCKER-USER` chain (iptables)
- Scoped by container subnet — only affects the OpenGrab container
- The container's own subnet is exempted for NAT/gateway access
- `127.0.0.0/8` is not blocked (Docker's embedded DNS at `127.0.0.11`)
- Idempotent, with `--dry-run`, `--log`, and `--remove` flags

```bash
sudo ./scripts/egress-lockdown.sh --dry-run    # preview
sudo ./scripts/egress-lockdown.sh --apply      # activate
sudo ./scripts/egress-lockdown.sh --remove     # deactivate
```

## Authentication

- Bearer token or HTTP-only cookie (`opengrab_token`, 30-day expiry)
- Token comparison uses `secrets.compare_digest` (constant-time, timing-attack resistant)
- `OPENGRAB_NO_AUTH=1` disables auth (trusted LAN or single-user desktop only)
- URL sanitization in logs: `?token=...` is masked in server output
- uvicorn `access_log=False` to prevent token leaks

## Rate Limiting

All write/expensive endpoints are rate-limited via slowapi. Limits are documented in the [API Reference](API.md).

## Prometheus Metrics Endpoint

The `/metrics` endpoint exposes Prometheus-format metrics **without authentication**.
This follows the industry convention: Prometheus scrape jobs use `GET /metrics` with
no credentials by default.

Protection is **network-level**, not application-level:

- **Docker**: place Prometheus and OpenGrab on the same Docker network. `/metrics` is
  only reachable from within that network — not exposed to the host or internet
  unless explicitly port-mapped.
- **Bare-metal / reverse proxy**: use a firewall (`iptables`, `nftables`) or
  reverse proxy (nginx, Caddy) to restrict access to the scraper's IP. Example
  nginx snippet:

  ```nginx
  location /metrics {
      allow 10.0.0.100;   # Prometheus server
      deny all;
      proxy_pass http://opengrab:8800;
  }
  ```

- **Tailscale / WireGuard**: the endpoint is only reachable by devices on the VPN.

If defense-in-depth is desired, place a reverse proxy with IP allowlisting in
front of OpenGrab and bind the application to `127.0.0.1`.

## Incognito Mode — Threat Model & Limitations

Incognito downloads skip history/dedup persistence, deliver the file to a
user-chosen folder, force-wipe the temp workdir (3-pass overwrite regardless of
the global `OPENGRAB_SECURE_DELETE` flag), harden yt-dlp (no on-disk cache,
generic User-Agent), and drop the DB row on every terminal state. What it does
**not** guarantee:

- **The overwrite is not a forensic erase.** 3-pass in-place overwrite only
  reliably destroys data on magnetic HDDs. On SSD/NVMe (wear-leveling), copy-on-write
  filesystems (Btrfs, ZFS, APFS) or anything with snapshots, stale copies may
  survive in unmapped blocks. For real guarantees use full-disk encryption or
  device-level secure-erase/TRIM. Incognito reduces casual recovery, not forensic.
- **DNS still leaks.** The SSRF gate (`_resolve_hostname`) runs
  `getaddrinfo()` through the system resolver **before** yt-dlp touches the URL,
  on every download, incognito or not. The destination host is visible to your
  DNS provider. Incognito does not add DoH/proxy (explicitly out of MVP scope).
- **`incognito_dir` is not allowlisted in server mode.** As with the existing
  "Save to…" flow, an authenticated client picks an arbitrary server-side path to
  write to. This is fine for desktop (server == client) but in a multi-user server
  deployment it lets a client write outside the default output dir. Treat the auth
  token as the trust boundary; do not expose incognito to untrusted clients.
- **In-memory job cards remain for the session.** The DB row is gone, but the
  live card (title, delivered path) stays in `AppState.jobs` until evicted or the
  app restarts. It is not written to disk.

## Reporting a Vulnerability

Please report security issues through one of these channels:

- **Preferred**: [GitHub Security Advisories](https://github.com/Skydope/OpenGrab/security/advisories/new)
- **Alternative**: **skydope [at] proton.me**

Do not open public issues until a fix is released.

---

## Español

### Defensa SSRF (Defensa en Profundidad)

OpenGrab actúa como proxy — yt-dlp hace requests HTTP desde tu servidor a URLs arbitrarias. Una defensa en dos capas previene Server-Side Request Forgery.

**Capa 1 (Aplicación)**: `_is_safe_url()` en `download.py` — validación estricta de esquema, hostname, IP literal y resolución DNS completa. Strict on DNS failure.

**Capa 2 (Red)**: `scripts/egress-lockdown.sh` — reglas DROP en DOCKER-USER del host, scopeadas por contenedor.

### Autenticación

- Token Bearer o cookie HTTP-only (`opengrab_token`, 30 días)
- Comparación con `secrets.compare_digest` (resistente a timing attacks)
- `OPENGRAB_NO_AUTH=1` desactiva auth (solo LAN confiable o escritorio)
- Sanitización de URL en logs

### Modo Incógnito — Modelo de Amenaza y Límites

Las descargas incógnito no persisten en historial/dedup, entregan el archivo a
una carpeta elegida, wipean el workdir temporal (sobreescritura 3-pass sin
depender del flag global), endurecen yt-dlp (sin caché en disco, User-Agent
genérico) y borran la fila de DB en todo estado terminal. Lo que **no** garantiza:

- **La sobreescritura no es borrado forense.** Solo destruye datos de forma
  confiable en HDD magnético. En SSD/NVMe, filesystems copy-on-write o con
  snapshots pueden quedar copias en bloques no mapeados. Para garantías reales:
  cifrado de disco o secure-erase/TRIM a nivel de dispositivo. Incógnito reduce
  la recuperación casual, no la forense.
- **El DNS igual se filtra.** El gate SSRF (`_resolve_hostname`) resuelve el host
  con el resolver del sistema **antes** de que yt-dlp toque la URL, en toda
  descarga. El host destino es visible para tu proveedor DNS. Incógnito no agrega
  DoH/proxy (fuera del alcance del MVP).
- **`incognito_dir` no tiene allowlist en modo servidor.** Igual que el flujo
  "Guardar en…", un cliente autenticado elige una ruta arbitraria del servidor.
  Está bien en escritorio (servidor == cliente); en un deploy multiusuario el
  token de auth es el límite de confianza — no expongas incógnito a clientes no
  confiables.

### Reportar Vulnerabilidades

Reportá issues de seguridad a través de estos canales:

- **Preferido**: [GitHub Security Advisories](https://github.com/Skydope/OpenGrab/security/advisories/new)
- **Alternativo**: **skydope [at] proton.me**

No divulgues issues públicos hasta que se resuelvan.
