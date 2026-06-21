<div align="center">

  <a href="https://github.com/Skydope/OpenGrab">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="Logo.png">
      <source media="(prefers-color-scheme: light)" srcset="Logo.png">
      <img src="Logo.png" alt="OpenGrab — self-hosted YouTube downloader" width="500" style="max-width:100%">
    </picture>
  </a>

  > Self-hosted YouTube downloader — paste a URL, get an MP4. Wraps yt-dlp + ffmpeg behind a clean web UI.

  [![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
  [![Version](https://img.shields.io/badge/version-1.5.0-green.svg)]()
  [![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
  [![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker)](https://hub.docker.com)

  [**English**](#) · [**Español**](#español)

</div>

<details>
<summary>Ver en Español</summary>

> **OpenGrab** — Descargador de YouTube auto-alojado. Pegás una URL, te llevás un MP4 (o MP3). Envoltorio web de yt-dlp + ffmpeg.
>
> Instalación rápida, ejemplos y guía de contribución disponibles más abajo en inglés. Podés abrir issues en español sin problema.

</details>

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [Architecture](#architecture)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Nginx (TLS)](#nginx-tls)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

OpenGrab is a self-hosted YouTube downloader for your homelab or LAN. You run it on a server (or your desktop), open the web UI, paste a YouTube URL, choose a quality preset, and get back an MP4 or MP3 file. No browser extensions, no shady websites, no desktop apps. Just your own server doing the work.

Built on top of [yt-dlp](https://github.com/yt-dlp/yt-dlp) (the actively maintained youtube-dl fork) and [ffmpeg](https://ffmpeg.org/) for muxing. The entire backend is a single FastAPI app with an inline vanilla frontend — zero npm, zero bundlers, zero CDN calls.

---

## Features

- Video downloads as **MP4** (best, 1080p, 720p, 480p) or **MP3** (audio-only)
- **Playlist support** — browse all videos in a playlist and download selected ones in batch
- Real-time progress via **Server-Sent Events** (SSE), no WebSocket complexity
- **Download history** persisted to a local JSON file
- Optional **token authentication** to restrict access (`OPENGRAB_TOKEN`)
- Configurable limits: max concurrent jobs, max file size
- **Pinned yt-dlp** in the image for reproducible builds; kept fresh via Dependabot. Optional opt-in auto-update on container start (`OPENGRAB_AUTOUPDATE=1`) for when you need the latest fix immediately
- Production-ready **nginx reverse proxy** config with TLS, SSE-friendly settings, and security headers

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Runtime | Python 3.11+ | — |
| Web framework | FastAPI + uvicorn | Async, type-driven |
| Download engine | yt-dlp | Runs in thread pool (blocking I/O) |
| Video processing | ffmpeg | System binary, muxing to MP4 |
| Frontend | Alpine.js + vanilla CSS | Embedded, no CDN, dark/light theme |
| Container | Docker + Compose | Single-image, healthcheck included |
| Reverse proxy | nginx | Optional, with TLS and SSE support |

---

## Getting Started

### Manual Setup

Prefer to configure things yourself? Here's the direct approach.

**Prerequisites**

- **Docker** >= 24.x (recommended) — or —
- **Python** 3.11+ with `pip`
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

## Usage

1. Open `http://localhost:8800` in your browser
2. If you set `OPENGRAB_TOKEN`, enter the token when prompted (stored in `sessionStorage`)
3. Paste a YouTube URL and click **Analizar**
4. Choose a quality preset: `best mp4`, `1080p`, `720p`, `480p`, or `solo audio · mp3`
5. Click **Descargar** — progress appears in the terminal-style output area
6. When complete, click the download link to save the file

For playlists, step 3 will detect the URL and show the playlist panel. Select the videos you want and click **Descargar playlist**.

---

## Architecture

```
opengrab/
├── app.py              # Entrypoint (~100 lines)
├── config.py           # Environment config
├── state.py            # AppState — jobs, history, locks
├── models.py           # Pydantic models
├── download.py         # yt-dlp wrappers
├── routes.py           # API endpoints (APIRouter)
├── static/
│   ├── index.html      # Alpine.js declarative UI
│   ├── style.css       # Dark/light theme
│   └── alpine.min.js   # Embedded, no CDN
├── tests/              # pytest tests
├── Dockerfile          # Non-root user, healthcheck
└── docker-compose.yml
```

**Key design decisions:**
- **Async backend + thread pool.** FastAPI handles HTTP, yt-dlp runs in `asyncio.to_thread()` to avoid blocking the event loop.
- **SSE over WebSockets.** Progress updates use `asyncio.Event` with a 2-second timeout polling loop. Simpler than WebSockets, zero extra dependencies.
- **In-memory state.** Job state is a typed Pydantic model in a `dict`. Download history is a JSON file on disk. Old jobs are evicted after 1 hour.
- **Offline-first frontend.** Alpine.js is embedded (~43KB), CSS uses variables for theming. No CDN calls, works entirely on LAN.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENGRAB_HOST` | No | `127.0.0.1` | Bind address (`0.0.0.0` for Docker) |
| `OPENGRAB_PORT` | No | `8800` | HTTP server port |
| `OPENGRAB_DIR` | No | `./downloads` | Output directory for downloaded files |
| `OPENGRAB_TOKEN` | No | — | If set, requires Bearer token on all `/api/*` routes |
| `OPENGRAB_MAX_JOBS` | No | `2` | Maximum concurrent downloads |
| `OPENGRAB_MAX_SIZE_MB` | No | `0` (unlimited) | Reject formats exceeding this size |
| `OPENGRAB_AUTOUPDATE` | No | `1` | Auto-update yt-dlp on container start |

See [`.env.example`](.env.example) for a ready-to-copy template.

---

## API Reference

All `/api/*` endpoints require authentication unless `OPENGRAB_NO_AUTH=1`. If `OPENGRAB_TOKEN` is empty, a token is auto-generated at startup and printed to the logs. Authenticate via:
- `Authorization: Bearer <token>` header
- `?token=<token>` query parameter
- `opengrab_token` HTTP-only cookie (set by `POST /api/auth`)

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/` | — | Web UI |
| `GET` | `/health` | — | Health check (returns `{"status":"ok","jobs_active":N}`) |
| `POST` | `/api/auth` | — | Authenticate and receive cookie — body: `{"token":"..."}` |
| `POST` | `/api/logout` | — | Clear auth cookie |
| `GET` | `/api/info?url=...` | 10/min | Fetch video metadata + available formats |
| `GET` | `/api/playlist?url=...` | — | Fetch playlist entries |
| `POST` | `/api/jobs` | 5/min | Create download job — body: `{"url":"...", "quality":"best"}` |
| `GET` | `/api/jobs/{id}/events` | — | SSE progress stream for a job |
| `GET` | `/api/jobs/{id}/file` | — | Download the completed file |
| `GET` | `/api/history?limit=20` | — | Download history as JSON |

### `POST /api/jobs`

**Request body:**
```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "quality": "best"
}
```

`quality` must be one of: `best`, `1080p`, `720p`, `480p`, `audio`.

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

---

## Español

> **OpenGrab** — Descargador de YouTube auto-alojado. Pegás una URL, te llevás un MP4 (o MP3).

### Descripción general

OpenGrab es un descargador de YouTube que corrés en tu propio servidor. Abrís la interfaz web, pegás una URL de YouTube, elegís calidad, y te descarga un MP4 o MP3. Nada de extensiones de navegador, sitios shady, ni apps de escritorio. Solo tu servidor haciendo el trabajo.

Usa [yt-dlp](https://github.com/yt-dlp/yt-dlp) como motor de descarga y [ffmpeg](https://ffmpeg.org/) para el muxing. Todo el backend es una app FastAPI con frontend vanilla inline — sin npm, sin bundlers, sin CDN.

### Instalacion manual

Si preferis configurar todo vos, este es el camino directo.

**Requisitos previos**

- **Docker** >= 24.x (recomendado) — o —
- **Python** 3.11+ con `pip`
- **ffmpeg** en el PATH (incluido en la imagen Docker; en bare metal: `apt install ffmpeg`, `brew install ffmpeg`, o `pacman -S ffmpeg`)

```bash
git clone https://github.com/skydope/opengrab.git
cd opengrab
cp .env.example .env
docker compose up -d
# → http://localhost:8800
```

> [!TIP]
> Para bare metal (sin Docker), corre `pip install -r requirements.txt` y despues `python app.py`. Asegurate de tener ffmpeg en el PATH.

### Uso básico

1. Abrí `http://localhost:8800`
2. Pegá una URL de YouTube y clic en **Analizar**
3. Elegí calidad (best, 1080p, 720p, 480p, o solo audio mp3)
4. Clic en **Descargar**

### Contribuir

Las contribuciones son bienvenidas. Podés abrir issues o PRs en español. Seguí los pasos de la sección [Contributing](#contributing) más arriba.

### Licencia

Distribuido bajo la [Licencia MIT](LICENSE).
