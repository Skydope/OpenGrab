<div align="center">

  <a href="https://github.com/Skydope/OpenGrab">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="Logo-dark.png">
      <source media="(prefers-color-scheme: light)" srcset="Logo.png">
      <img src="Logo.png" alt="OpenGrab" width="520" style="max-width:100%">
    </picture>
  </a>

  > Self-hosted video & audio downloader — paste a URL from any site, get an MP4. Wraps yt-dlp (1800+ sites) + ffmpeg behind a clean web UI.

  [![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
  [![Version](https://img.shields.io/github/v/tag/Skydope/OpenGrab?label=version)](https://github.com/Skydope/OpenGrab/releases/latest)
  [![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org)
  [![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://github.com/Skydope/OpenGrab/pkgs/container/opengrab)
  [![Stars](https://img.shields.io/github/stars/Skydope/OpenGrab?style=social)](https://github.com/Skydope/OpenGrab/stargazers)

  **EN** · [ES](README.es.md)

</div>

> [!CAUTION]
> **OpenGrab is a self-hosted tool for personal use.** The authors are not
> responsible for how you use it. You are solely responsible for complying
> with the Terms of Service of the platforms you interact with and applicable
> copyright laws. Use only with content you own, is licensed for reuse, or
> under fair use provisions.

## Highlights

- Universal support (1800+ sites via yt-dlp)
- Quality presets: **best**, 1080p, 720p, 480p, MP3
- Full playlists + **Watch mode** (channels with deduplication)
- Real-time progress via SSE
- SQLite (WAL) persistence + crash recovery
- **Two-layer SSRF protection** (app-level DNS + host-level firewall)
- Desktop app (Windows installer with bilingual wizard)
- Production-ready nginx + TLS config
- yt-dlp hot-swap and reproducible builds

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Skydope/OpenGrab.git && cd OpenGrab
cp .env.example .env
docker compose up -d
```

Open `http://localhost:8800`

### Desktop

Download the installer from [Releases](https://github.com/Skydope/OpenGrab/releases/latest).

See [docs/INSTALL.md](docs/INSTALL.md) for all options.

## Documentation

- [Installation](docs/INSTALL.md)
- [Production Deployment](docs/DEPLOY.md)
- [API Reference](docs/API.md)
- [Security](docs/SECURITY.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Desktop App](#desktop-app)
- [Usage](#usage)
- [Architecture](#architecture)
- [Security Model](#security-model)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Nginx (TLS)](#nginx-tls)
- [Contributing](#contributing)
- [Documentation](#documentation)
- [License](#license)

---

## Overview

OpenGrab is a self-hosted video & audio downloader for your homelab or LAN. Paste a URL from practically any site — YouTube, Vimeo, TikTok, Instagram, X/Twitter, Bandcamp, SoundCloud, and thousands more — choose a quality preset, and get back an MP4 or MP3 file. No browser extensions, no shady websites. Just your own server — or a [Windows desktop app](#desktop-app) if you prefer.

Built on top of [yt-dlp](https://github.com/yt-dlp/yt-dlp) (engine that supports 1800+ sites) and [ffmpeg](https://ffmpeg.org/) for muxing. The entire backend is a single FastAPI app with an inline vanilla frontend — zero npm, zero bundlers, zero CDN calls.

---

## Features

- Video downloads as **MP4** (best, 1080p, 720p, 480p) or **MP3** (audio-only)
- **Universal support** — works with any site yt-dlp can handle (1800+), not just YouTube
- **Playlist support** — browse all videos in a playlist and download selected ones in batch
- **Clips** — download just a time range: pick start/end on a dual-handle timeline or type `MM:SS`; works for video and mp3, fetching only the needed fragments on HLS/DASH
- Real-time progress via **Server-Sent Events** (SSE), no WebSocket complexity
- **Download history** persisted to SQLite (WAL mode), with crash recovery
- Optional **token authentication** to restrict access (`OPENGRAB_TOKEN`)
- **Human-friendly error messages** with a retry button for failed downloads
- **yt-dlp hot-swap** — update the download engine from the UI or on desktop start, without rebuilding
- Configurable limits: max concurrent jobs, max file size, total disk budget
- **Layered SSRF protection** — app-level DNS validation blocks private IPs, loopback, and cloud metadata endpoints; optional host-level egress firewall closes DNS rebinding at the network layer
- **Pinned yt-dlp** in the image for reproducible builds; kept fresh via Dependabot. Optional opt-in auto-update on container start (`OPENGRAB_AUTOUPDATE=1`) for when you need the latest fix immediately
- Production-ready **nginx reverse proxy** config with TLS, SSE-friendly settings, and security headers
- **Windows desktop app** — native window via pywebview + WebView2, system tray, single-instance, installer wizard (Spanish/English)

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Runtime | Python 3.12+ | — |
| Web framework | FastAPI + uvicorn | Async, type-driven |
| Download engine | yt-dlp | Runs in thread pool (blocking I/O) |
| Video processing | ffmpeg | System binary, muxing to MP4 |
| Data | SQLite (WAL mode) | Job persistence, crash recovery, history |
| Frontend | Alpine.js + vanilla CSS | Embedded, no CDN, dark/light theme |
| Container | Docker + Compose | Single-image, healthcheck included |
| Reverse proxy | nginx | Optional, with TLS and SSE support |
| Desktop | pywebview + pystray | Windows app, system tray, WebView2 |

---

## Getting Started

### Manual Setup

Prefer to configure things yourself? Here's the direct approach.

**Prerequisites**

- **Docker** >= 24.x (recommended) — or —
- **Python** 3.12+ with `pip`
- **ffmpeg** on PATH (included in the Docker image; on bare metal: `apt install ffmpeg`, `brew install ffmpeg`, or `pacman -S ffmpeg`)

```bash
git clone https://github.com/skydope/opengrab.git
cd opengrab

# Copy and configure environment
cp .env.example .env
# Edit .env if you want to set a token or change defaults

# Start with Docker Compose
docker compose up -d
# http://localhost:8800
```

> [!TIP]
> For bare-metal usage (no Docker), run `pip install -r requirements.txt` and then `python app.py`. Make sure ffmpeg is on your PATH.

---

## Desktop App

| Platform | Download | Format |
|----------|----------|--------|
| Windows | `OpenGrab-Setup.exe` | Installer wizard (Spanish/English) |
| Linux | `OpenGrab-x86_64.AppImage` | Portable executable |
| macOS | `OpenGrab-macos.zip` | App bundle |

All downloads include bundled ffmpeg and yt-dlp with hot-swap support.

Download from the [latest release](https://github.com/Skydope/OpenGrab/releases/latest).

### Platform notes

**Windows:** Installer wizard with Recommended (defaults) and Advanced (custom folder, port,
password, auto-start) modes. Native window via WebView2 (pywebview), silently installed if
missing. System tray support.

**Linux:** AppImage — `chmod +x OpenGrab-x86_64.AppImage && ./OpenGrab-x86_64.AppImage`.
Opens your default browser (no native window). System tray via D-Bus/pystray. Requires a
desktop environment with an indicator area (GNOME, KDE, XFCE, etc.).

**macOS:** Unzip and run `OpenGrab.app`. Native window via Cocoa WebKit (pywebview). System
tray support. May require right-click → Open on first launch (Gatekeeper).

---

## Usage

1. Open `http://localhost:8800` in your browser
2. If you set `OPENGRAB_TOKEN`, enter the token when prompted (stored in an HTTP-only cookie, 30-day expiry)
3. Paste a URL from any supported site and click **Analizar**
4. Choose a quality preset: `best mp4`, `1080p`, `720p`, `480p`, or `solo audio · mp3`
5. Click **Descargar** — progress appears in the terminal-style output area
6. When complete, click the download link to save the file

For playlists, step 3 will detect the URL and show the playlist panel. Select the videos you want and click **Descargar playlist**.

---

## Architecture

```
opengrab/
├── app.py                    # FastAPI entrypoint (uvicorn, middleware, routers)
├── config.py                 # Settings: env > DB table > config.ini > defaults
├── state.py                  # AppState — runtime jobs, events, eviction
├── db.py                     # SQLite data layer (WAL, migrations, crash recovery)
├── models.py                 # Pydantic models
├── download.py               # yt-dlp wrappers, SSRF gate, error mapping
├── history_store.py          # History queries, single delete, bulk clear
├── storage_manager.py        # Disk usage, workdir cleanup, job eviction
├── library_path_resolver.py  # Output path templates, dedup, file moves
├── secure_delete.py          # 3-pass secure file wipe
├── metrics.py                # Prometheus metrics
├── i18n.py                   # Server-side translations (flat JSON)
├── logging_setup.py          # Plain-text or structured JSON logging
├── desktop.py                # Desktop launcher (pywebview + tray)
├── engine_update.py          # yt-dlp hot-swap via PyPI wheel
├── routers/                  # API endpoints, one module per domain
│   ├── jobs.py               #   downloads, SSE progress, files
│   ├── playlist.py           #   playlist listing + batch enqueue
│   ├── channels.py           #   Watch mode
│   ├── history.py
│   ├── storage.py
│   ├── settings.py
│   ├── backup.py             #   JSON export/import
│   ├── engine.py             #   yt-dlp update
│   └── system.py             #   auth, health, metrics, UI
├── static/
│   ├── index.html            # Alpine.js SPA
│   ├── style.css             # Dark/light theme, WCAG AA
│   ├── alpine.min.js         # Embedded (~44KB), no CDN
│   ├── js/                   # Frontend modules (no bundler)
│   └── i18n/                 # UI translations (en, es)
├── tests/                    # pytest suite
├── scripts/                  # Checks, installers, egress firewall, backups
├── docs/                     # Install, deploy, API, security, architecture
├── docker/                   # Container entrypoint
├── vendor/                   # App icon (ffmpeg is copied here at build time)
├── nginx/                    # Reverse proxy TLS config
├── Dockerfile                # Non-root user, healthcheck
├── docker-compose.yml
├── OpenGrab.spec             # PyInstaller build spec
└── opengrab.iss              # Inno Setup installer wizard
```

More design detail and trade-offs in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Key design decisions:**
- **Async backend + thread pool.** FastAPI handles HTTP, yt-dlp runs in `asyncio.to_thread()` to avoid blocking the event loop.
- **SSE over WebSockets.** Progress updates use `asyncio.Event` with a 2-second timeout polling loop. Simpler than WebSockets, zero extra dependencies.
- **RAM + SQLite state.** Job state (progress, speed, ETA) lives in-memory for real-time SSE delivery. State transitions (queued → downloading → done) are persisted to SQLite (WAL mode). History reads come from the database. Old in-memory jobs are evicted after 1 hour.
- **Offline-first frontend.** Alpine.js is embedded (~44KB), CSS uses variables for theming. No CDN calls, works entirely on LAN.

---

## Security Model

OpenGrab employs a **2-layer SSRF defense** because it acts as a proxy: yt-dlp makes HTTP requests from your server to arbitrary URLs.

### Layer 1 — App-level DNS validation (`download.py`)

The gate function `_is_safe_url()` runs before every URL reaches yt-dlp:

1. **Scheme lock**: only `http` and `https` (blocks `file://`, `ftp://`, `javascript:`, `data:`)
2. **Hostname blocklist**: `localhost`, `0.0.0.0`, `::1`, `.local`, `.localhost`
3. **IP literal check**: if the URL contains an IP address, validates against:
   - Private (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
   - Loopback (`127.0.0.0/8`, `::1`)
   - Link-local (`169.254.0.0/16` — includes cloud metadata `169.254.169.254`)
   - Reserved, multicast, unspecified
4. **DNS resolution**: if the URL has a domain name, resolves **all** IPs (A + AAAA) with
   `getaddrinfo` and validates each. This closes the classic bypass where a domain like
   `evil.com` has an A record pointing to `10.0.0.5`.
5. **Strict on failure**: if DNS resolution fails, the request is blocked — a transient
   false negative is safer than a silent SSRF bypass. ULA IPv6 (`fc00::/7`) and
   IPv4-mapped IPv6 (`::ffff:10.0.0.5`) are covered by `is_private` (CPython 3.12+).

Error messages are differentiated: "no se pudo resolver el host" (DNS failure) vs
"el host resuelve a una IP privada o reservada" (SSRF attempt).

> [!NOTE]
> Layer 1 cannot fully prevent DNS rebinding (TOCTOU). Between the gate's DNS resolution
> and yt-dlp's own connection, a malicious DNS server could change the answer. Layer 2
> closes this gap.

### Layer 2 — Host-level egress firewall (optional, recommended)

For defense in depth, block the container's egress to private ranges at the network level:

```bash
# Preview the rules before applying
sudo ./scripts/egress-lockdown.sh --dry-run

# Apply to DOCKER-USER chain (scoped to the OpenGrab container only)
sudo ./scripts/egress-lockdown.sh --apply

# Optional: log blocked attempts (rate-limited, kern.log)
sudo ./scripts/egress-lockdown.sh --apply --log

# Verify
sudo ./scripts/egress-lockdown.sh --list

# Remove
sudo ./scripts/egress-lockdown.sh --remove
```

**Design decisions:**
- Rules live in the **host's** `DOCKER-USER` chain (evaluated before Docker's own rules).
  No `NET_ADMIN` capability needed in the container — the existing non-root `USER opengrab`
  model is preserved. Works with Docker rootless as long as iptables runs on the host.
- **Scoped by container subnet** (detected via `docker inspect`). Only blocks traffic from
  the OpenGrab container — your other homelab services (Grafana, Home Assistant, etc.)
  are not affected.
- The container's own subnet is **exempted** (`RETURN` rule) to allow gateway access
  (NAT to internet) and peer communication.
- `127.0.0.0/8` is **not blocked** — Docker's embedded DNS resolver lives at
  `127.0.0.11` inside the container's network namespace and doesn't traverse DOCKER-USER.
  Loopback SSRF is already covered by Layer 1.

**IPv6:** the script adds `ip6tables` rules for ULA (`fc00::/7`), link-local
(`fe80::/10`), and loopback (`::1`) if Docker has IPv6 enabled. As a simpler
alternative for a video downloader (CDN delivery is IPv4 in practice), disable
IPv6 on the container by uncommenting in `docker-compose.yml`:

```yaml
sysctls:
  - net.ipv6.conf.all.disable_ipv6=1
```

> [!WARNING]
> `DOCKER-USER` rules are ephemeral — they're lost when you recreate the Docker
> network or restart the Docker daemon. Re-run `--apply` (it's idempotent) or tie
> it to a systemd hook that fires after `docker.service`.

### Summary

| Threat | Layer 1 (app) | Layer 2 (network) |
|--------|--------------|-------------------|
| IP literal to private range | Blocked | Dropped |
| Domain → private IP | Blocked (DNS resolve) | Dropped |
| DNS rebinding | Not covered (TOCTOU) | Dropped |
| `file://`, `ftp://`, etc. | Blocked (scheme lock) | — |
| Cloud metadata (`169.254.169.254`) | Blocked (link-local) | Dropped |

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENGRAB_HOST` | No | `127.0.0.1` | Bind address (`0.0.0.0` for Docker) |
| `OPENGRAB_PORT` | No | `8800` | HTTP server port |
| `OPENGRAB_DIR` | No | `./downloads` | Output directory for downloaded files |
| `OPENGRAB_TOKEN` | No | — | If set, requires Bearer token on all `/api/*` routes. If empty, auto-generates one at startup. |
| `OPENGRAB_NO_AUTH` | No | — | Set to `1` to disable authentication (dev/local only) |
| `OPENGRAB_MAX_JOBS` | No | `2` | Maximum concurrent downloads |
| `OPENGRAB_MAX_SIZE_MB` | No | `0` (unlimited) | Per-file size limit |
| `OPENGRAB_MAX_TOTAL_MB` | No | `0` (unlimited) | Disk budget — refuses new jobs via HTTP 507 when exceeded |
| `OPENGRAB_AUTOUPDATE` | No | `0` | Auto-update yt-dlp on container start. Opt-in. |
| `OPENGRAB_YTDLP_VERSION` | No | — | Pin a specific yt-dlp version when auto-update is enabled |
| `OPENGRAB_TRUST_XFF` | No | `0` | Trust `X-Forwarded-For` for rate limiting (enable behind nginx) |
| `OPENGRAB_CONFIG` | No | — | Custom path to `config.ini` (desktop mode) |

See [`.env.example`](.env.example) for a ready-to-copy template.

---

## API Reference

All `/api/*` endpoints require authentication unless `OPENGRAB_NO_AUTH=1`. If `OPENGRAB_TOKEN` is empty, a token is auto-generated at startup and printed to the logs. Authenticate via:
- `Authorization: Bearer <token>` header
- `?token=<token>` query parameter
- `opengrab_token` HTTP-only cookie (set by `POST /api/auth`, 30-day expiry)

The full endpoint list (jobs, playlists, history, storage, settings, channels, backup and system), with rate limits and request bodies, is in [docs/API.md](docs/API.md). The two endpoints you will use most:

### `POST /api/jobs`

**Request body:**
```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "quality": "best"
}
```

`quality` must be one of: `best`, `1080p`, `720p`, `480p`, `audio` (`worst` is also accepted).

**Response:**
```json
{"job_id": "a1b2c3d4e5f6"}
```

### `GET /api/jobs/{id}/events`

SSE stream. Each event is a JSON snapshot:
```json
{"status":"downloading","percent":45.3,"speed":"12.3MiB/s","eta":"00:42","note":"","filename":"","error":""}
```

On completion: `"status": "done"` with `"filename"` set.
On error: `"status": "error"` with `"error"` containing the message.

---

## Nginx (TLS)

A production-ready nginx config is included at `nginx/opengrab.conf`. Drop it into your nginx `conf.d/` directory. It handles:

- HTTP → HTTPS redirect
- TLS termination (point `ssl_certificate` to your certificate)
- SSE-friendly settings (`proxy_buffering off`, 3600s timeouts)
- Security headers (HSTS, X-Frame-Options, X-Content-Type-Options)
- Docker DNS resolver so nginx starts even if opengrab is momentarily down

---

## Contributing

Contributions are welcome. Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Commit your changes following [Conventional Commits](https://www.conventionalcommits.org/)
4. Push and open a Pull Request

> [!NOTE]
> Issues and PRs in Spanish are welcome / Los issues y PRs en español son bienvenidos.

---

## License

Distributed under the [MIT License](LICENSE). See `LICENSE` for details.
