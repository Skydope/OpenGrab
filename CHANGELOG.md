# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] — 2026-06-21

### Security

- Token auto-generation now triggers when `OPENGRAB_TOKEN` is empty (not only when unset), closing the insecure default under `docker compose up` where Compose passes the variable as an empty string
- Removed the `127.0.0.1` bypass in `require_auth`: behind a reverse proxy on the same host (`proxy_pass http://127.0.0.1:8800`) it disabled auth for all clients
- `/health` is public again but no longer exposes `jobs_active` (info leak)
- New `OPENGRAB_NO_AUTH=1` escape hatch to disable auth explicitly for local dev

### Fixed

- `client_no_auth` test fixture migrated to `OPENGRAB_NO_AUTH=1` (empty token no longer means "no auth")
- `.env.example` and README updated: empty token now documents auto-generation, not "no auth"

### Added

- `test_token_autogen_on_empty`, `test_no_auth_escape_hatch`, `test_no_localhost_bypass` (unit test on `require_auth` with a crafted 127.0.0.1 client — catches the bypass regression), `test_health_public_no_jobs_active`

## [1.2.0] — 2026-06-21

### Security

- Token autogenerado al arranque si `OPENGRAB_TOKEN` no está configurado, cerrando el default inseguro del compose
- Content-Disposition usa RFC 5987 (`filename*=UTF-8''`) para títulos con caracteres Unicode (ñ, á, etc.)
- Path containment verificado antes de `exists()` en file serving — evita info leak de "el archivo existe pero no te lo doy"
- Rate limit agregado a `/api/playlist` (10/min)
- `/health` requiere autenticación (con bypass para `127.0.0.1` para el healthcheck de Docker)
- Control characters (`\x00-\x1f`, `\x7f`) eliminados de `_safe_name` y del header Content-Disposition
- `OPENGRAB_TRUST_XFF` documentado en `docker-compose.yml` con advertencia del footgun

### Added

- 3 nuevos tests: path traversal (403), filename Unicode RFC 5987, control chars stripping (42 tests total)

### Fixed

- `_safe_name` ya no deja pasar newlines internos en títulos de video
- `/api/playlist` ahora requiere parámetro `request` para que slowapi funcione correctamente
- Tests: `client` fixture ahora incluye cookie de auth por defecto; `client_no_auth` para tests sin token

### Added

- Interactive CLI installer (`install.py`) with recommended and advanced modes
- `pyproject.toml` with dev dependencies and pytest-asyncio configuration
- `state.py`: AppState class replacing global mutable state with dependency injection
- Rate limiting key function using `X-Forwarded-For` for correct per-client limits behind nginx
- 39 pytest tests (up from 29), including rate limiting, eviction, and SSE generator tests

### Changed

- Download filepath now uses yt-dlp's `requested_downloads` canonical path instead of glob+mtime
- Token comparison uses `secrets.compare_digest` for timing-attack resistance
- Docker auto-update uses `pip install --user` to work under non-root user
- Workdir cleanup deferred to eviction loop (1h) instead of immediate deletion on file download
- `.env.example` defaults to `127.0.0.1` (localhost) with security warning comments
- Cookie `secure` flag reads `X-Forwarded-Proto` for correct behavior behind reverse proxy
- Color contrast improved to meet WCAG AA (4.5:1) for dark and light themes
- Nginx config updated to ECDHE-only ciphers and Cross-Origin headers

### Fixed

- Config env vars (`PORT`, `MAX_JOBS`, `MAX_SIZE_MB`) no longer crash on invalid values
- `MAX_JOBS=0` no longer permanently blocks all job creation
- `_sanitize_url` now strips newlines to prevent log injection
- `extract_info` return value checked for `None` in download runner
- Audio-only download note corrected from "mp4" to "mp3"
- Content-Disposition header escapes double quotes in filenames
- `filesize=0` no longer incorrectly deprioritized in format sorting
- `view_count` defaults to 0 instead of `None` (prevented frontend "NaN")
- Rate-limited responses now include security headers
- SSE generator extracted to testable `_job_events_stream` function
- Auth check on page load uses `/api/history` instead of hitting yt-dlp
- Playlist batch download retries on rate limit (429) with 20s delay
- `asyncio.create_task` references now tracked to prevent garbage collection
- `threading.Lock` protects concurrent history file writes from thread pool

## [1.0.0] — 2026-06-21

### Added

- Web UI with Alpine.js declarative frontend (dark/light theme, keyboard accessible)
- Quality presets: best MP4, 1080p, 720p, 480p, audio-only MP3
- Format preview table with codec info and estimated file sizes
- Copy yt-dlp command button with clipboard API
- Real-time download progress via Server-Sent Events (SSE) with auto-retry
- Multi-platform support: YouTube, Vimeo, Twitter/X, TikTok, Instagram
- Playlist batch download with progress counter
- Download history persisted to JSON file (max 500 entries)
- Token authentication via Bearer header, HTTP-only cookie (30-day), or query param
- Auth endpoints: `POST /api/auth` and `POST /api/logout`
- Configurable limits: max concurrent jobs, max file size, rate limiting (slowapi)
- Automatic eviction of completed jobs from memory after 1 hour
- Docker image with non-root user, auto-updating yt-dlp, and healthcheck
- Docker Compose configuration
- Nginx reverse proxy config with TLS, CSP, Referrer-Policy, Permissions-Policy, SSE support
- 29 pytest tests covering helpers, models, auth, and API endpoints

### Security

- HTTP-only cookie with SameSite=Lax and conditional Secure flag
- URL sanitization in server logs to prevent token leaks
- Path containment check on file serving
- Rate limiting: 30/min default, 10/min on `/api/info`, 5/min on `/api/jobs`
- Graceful fallback if static files are missing

[1.2.0]: https://github.com/skydope/opengrab/releases/tag/v1.2.0
[1.1.0]: https://github.com/skydope/opengrab/releases/tag/v1.1.0
[1.0.0]: https://github.com/skydope/opengrab/releases/tag/v1.0.0
