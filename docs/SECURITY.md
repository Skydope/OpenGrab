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

## Reporting a Vulnerability

Please report security issues privately to **skydope [at] proton.me**. Do not open public issues until a fix is released.

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

### Reportar Vulnerabilidades

Reportá issues de seguridad de forma privada a **skydope [at] proton.me**. No divulgar issues públicos hasta que se resuelvan.
