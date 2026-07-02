# Security Policy

## Threat Model

OpenGrab runs in two deployment modes with different trust boundaries. Everything
else in this document hangs off this distinction.

**Desktop mode** (`OPENGRAB_DESKTOP=1`): server and client are the same person on
the same machine. The web UI is a local window (pywebview) talking to
`127.0.0.1`. Filesystem-touching features (pick any folder for incognito
delivery, "Save to…" move, open-folder) are legitimate here because the "remote
client" is the machine's owner.

**Server mode** (Docker/LAN): the auth token is the trust boundary. Anyone
holding the token is trusted to *download media and read their results* — and
nothing more. Token holders must NOT gain: arbitrary filesystem writes outside
`OPENGRAB_DIR` (hence incognito destinations are confined and `/move` is
desktop-only), arbitrary reads (file-serving endpoints validate paths against
allowed roots), or the ability to make the server contact internal networks
(SSRF gate + optional egress firewall).

### In scope (what OpenGrab defends against)

| Threat | Control |
|---|---|
| SSRF via user-supplied URLs (yt-dlp requests from the server) | Two-layer defense: app-level gate (`_is_safe_url`, full DNS validation) + host-level egress firewall (see below) |
| Unauthenticated access to jobs, files, settings | Token auth on every route, enforced by a contract test that enumerates all routes (`tests/test_auth_contract.py`) |
| Path traversal on file-serving endpoints | `resolve()` + `is_relative_to()` against allowed roots |
| SQL injection | Parameterized queries everywhere; dynamic column names only from internal whitelists (`_UPDATABLE`) |
| Rate-based abuse of expensive endpoints | slowapi limits on write/expensive routes |
| Supply chain (yt-dlp, deps, images) | Exact yt-dlp pin, runtime auto-update off by default, Trivy on the Docker image, pip-audit + bandit in CI, SBOM/attestations on multi-arch builds, VirusTotal gate on release binaries |
| Filename/header injection when serving downloads | Filename sanitization (`_safe_name`) + RFC 5987 Content-Disposition encoding |

### Out of scope (explicitly NOT defended)

- **Malicious token holders in server mode beyond the boundary above.** A token
  holder can fill the disk up to `max_total_mb`, enumerate history, and delete
  entries. OpenGrab is single-tenant by design; don't share tokens with people
  you don't trust with your download library.
- **Forensic-grade deletion.** See the incognito section: 3-pass overwrite helps
  on magnetic HDDs only. Use full-disk encryption for real guarantees.
- **Network-level privacy.** DNS lookups and TLS SNI reveal destination hosts to
  the network. Incognito mode does not add proxy/DoH.
- **A compromised yt-dlp or extractor ecosystem.** OpenGrab executes yt-dlp
  in-process; the exact-version pin plus disabled auto-update is the mitigation,
  not a sandbox. Host egress filtering limits blast radius.
- **Physical access to the machine.** DB, config, and downloads are unencrypted
  at rest.

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
- **`incognito_dir` is confined to the output dir in server mode.** In desktop
  mode (server == client) the user picks any local folder — that is the feature.
  In server mode (Docker/LAN), the destination must resolve inside `OPENGRAB_DIR`;
  arbitrary server-side paths are rejected with 400 (an authenticated remote
  client must not gain a write primitive outside the downloads volume). The
  related "Save to…" endpoint (`/api/jobs/{id}/move`) is desktop-only for the
  same reason.
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

### Modelo de Amenazas

OpenGrab corre en dos modos con límites de confianza distintos; todo lo demás de
este documento cuelga de esa distinción.

**Modo escritorio** (`OPENGRAB_DESKTOP=1`): servidor y cliente son la misma
persona en la misma máquina. Las features que tocan el filesystem (carpeta libre
para incógnito, "Guardar en…", abrir carpeta) son legítimas porque el "cliente
remoto" es el dueño de la máquina.

**Modo servidor** (Docker/LAN): el token de auth es el límite de confianza.
Quien tiene el token puede *descargar y leer sus resultados* — y nada más. Un
portador del token NO debe ganar: escrituras arbitrarias fuera de
`OPENGRAB_DIR` (por eso incógnito queda acotado y `/move` es solo-escritorio),
lecturas arbitrarias (los endpoints de archivos validan contra raíces
permitidas), ni la capacidad de hacer que el servidor contacte redes internas
(gate SSRF + firewall de egreso opcional).

**Dentro del alcance:** SSRF vía URLs de usuario (defensa en dos capas), acceso
no autenticado (auth en toda ruta, verificado por un test de contrato que
enumera todas las rutas), path traversal en endpoints de archivos, inyección
SQL (queries parametrizadas, columnas dinámicas solo de whitelists internas),
abuso de endpoints costosos (rate limiting), y supply chain (pin exacto de
yt-dlp, auto-update off por default, Trivy en la imagen, bandit + pip-audit en
CI, SBOM/attestations, gate de VirusTotal en binarios de release).

**Fuera del alcance (explícitamente NO se defiende):** portadores maliciosos
del token más allá del límite descripto (OpenGrab es single-tenant por diseño),
borrado con garantías forenses (ver sección incógnito), privacidad a nivel de
red (DNS/SNI revelan el destino), un yt-dlp comprometido (el pin es la
mitigación, no un sandbox; el egress filtering limita el radio de daño), y
acceso físico a la máquina (DB y descargas sin cifrar en reposo).

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
- **`incognito_dir` queda acotado al directorio de salida en modo servidor.**
  En escritorio (servidor == cliente) el usuario elige cualquier carpeta local —
  esa es la feature. En modo servidor (Docker/LAN), el destino debe resolver
  dentro de `OPENGRAB_DIR`; rutas arbitrarias del servidor se rechazan con 400
  (un cliente remoto autenticado no debe ganar una primitiva de escritura fuera
  del volumen de descargas). El endpoint "Guardar en…" (`/api/jobs/{id}/move`)
  es solo-escritorio por el mismo motivo.

### Reportar Vulnerabilidades

Reportá issues de seguridad a través de estos canales:

- **Preferido**: [GitHub Security Advisories](https://github.com/Skydope/OpenGrab/security/advisories/new)
- **Alternativo**: **skydope [at] proton.me**

No divulgues issues públicos hasta que se resuelvan.
