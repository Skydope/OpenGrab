# ytgrab

Self-hosted YouTube downloader — paste a URL, get an MP4 (or MP3). Wraps `yt-dlp` + `ffmpeg` behind a clean web UI.

Designed for homelab / LAN use. Single Python file, zero external frontend dependencies.

## Features

- Download YouTube videos as **MP4** (best, 1080p, 720p, 480p) or **MP3** (audio-only)
- Real-time progress via Server-Sent Events (SSE)
- **Playlist support** — list all videos and download selected ones
- **Download history** persisted to JSON
- Optional **token authentication** (`YTGRAB_TOKEN`)
- Configurable limits: max concurrent jobs, max file size
- Dockerized with auto-updating `yt-dlp` on container start
- Production-ready nginx reverse proxy config included

## Quick Start

```bash
# Docker (recommended)
docker compose up -d
# → http://localhost:8800

# Bare metal
pip install -r requirements.txt
# ffmpeg must be on PATH
python app.py
# → http://127.0.0.1:8800
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `YTGRAB_HOST` | `127.0.0.1` | Bind address |
| `YTGRAB_PORT` | `8800` | Listen port |
| `YTGRAB_DIR` | `./downloads` | Output directory |
| `YTGRAB_TOKEN` | *(none)* | If set, requires `Authorization: Bearer <token>` on all API calls |
| `YTGRAB_MAX_JOBS` | `2` | Maximum concurrent downloads |
| `YTGRAB_MAX_SIZE_MB` | `0` (unlimited) | Reject formats exceeding this size |
| `YTGRAB_AUTOUPDATE` | `1` | Auto-update yt-dlp on container start |

## Architecture

- **Backend**: FastAPI + uvicorn (async)
- **Download engine**: yt-dlp (blocking, runs in thread pool)
- **Progress**: SSE stream driven by `asyncio.Event` (near-zero latency)
- **Frontend**: Inline HTML/CSS/JS — no npm, no bundler, no CDN
- **Storage**: In-memory job state + JSON file for download history

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | Web UI |
| `GET` | `/health` | No | Health check (active jobs count) |
| `GET` | `/api/info?url=...` | Token | Fetch video metadata |
| `GET` | `/api/playlist?url=...` | Token | Fetch playlist entries |
| `POST` | `/api/jobs` | Token | Create download job `{url, quality}` |
| `GET` | `/api/jobs/{id}/events` | Token | SSE progress stream |
| `GET` | `/api/jobs/{id}/file` | Token | Download completed file |
| `GET` | `/api/history` | Token | Download history (JSON) |

## Nginx (TLS)

Drop `nginx/ytgrab.conf` into your nginx `conf.d/`. It handles:
- HTTP → HTTPS redirect
- TLS with certificate
- SSE-friendly settings (`proxy_buffering off`, long timeouts)
- Security headers (HSTS, X-Frame-Options, etc.)

## Requirements

- Python 3.11+
- `ffmpeg` on PATH (apt, pacman, brew)
- Or Docker (recommended)
