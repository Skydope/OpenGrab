# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] — 2026-06-22

### Added (Desktop — Windows installer)

- **OpenGrab-Setup.exe**: wizard de instalación con Inno Setup (59.6 MB), 7 páginas:
  modo Recomendada (Next-Next-Finish) y Avanzada (carpeta de descargas, puerto,
  contraseña, auto-start con Windows). Bilingüe (español + inglés).
- **WebView2 detection**: `_webview2_available()` detecta el runtime real. Si no está
  instalado, cae al navegador con un aviso; el wizard lo instala silenciosamente.
- **MessageBox UX**: los errores del launcher en modo `--windowed` ahora se muestran
  en ventanas visibles (antes `print()` no tenía destino y el usuario veía "no abrió nada").
- **config.ini support**: `config.py` lee `%APPDATA%\OpenGrab\config.ini` como fuente de
  defaults. El wizard lo escribe con las elecciones del usuario. Las variables de entorno
  siempre tienen precedencia (Docker sin cambios).
- **App icon**: `opengrab.ico` convertido desde `Logo.png`, usado en el `.exe`, accesos
  directos y wizard.
- **Single-instance UX**: si el usuario hace doble clic dos veces, un MessageBox avisa
  que la app ya está corriendo (en vez de salir en silencio).

### Added (Desktop — launcher)

- `desktop.py`: entrypoint de escritorio (puerto efímero, `NO_AUTH`, carpeta Descargas,
  single-instance crash-safe vía named mutex/flock, health-gate, pywebview con fallback
  a navegador)
- `engine_update.py`: hot-swap de yt-dlp vía wheel en `%LOCALAPPDATA%` + `sys.path`
  (spike verificado en Linux y Windows: `collect_all` deja yt-dlp suelto → el override gana)
- `OpenGrab.spec`: build PyInstaller onedir (`collect_all yt_dlp`, `windowed`, sin UPX)
- `POST /api/engine/update` + botón "Actualizar motor (yt-dlp)" en la UI
- `config.resource_path()` + `_STATIC_DIR` frozen-aware; `ffmpeg_location` bundleado
  con guard (no afecta Docker/dev)
- `tests/test_desktop.py`: 20 tests de la lógica de escritorio y hot-swap

### Added (SQLite — PR-0, data layer, sin cablear)

- `db.py`: capa de acceso SQLite (conexión WAL + lock serializado). Tabla única `jobs`
  (cola + historial), `channels` y `downloaded_urls` para watch mode. CRUD de jobs,
  transiciones, `mark_interrupted()` (devuelve workdirs para limpiar), dedup
  (`record_download`/`is_downloaded`), `prune_history`, e import del `history.json` legacy.
- `tests/test_db.py`: 17 tests en `:memory:`/temp (roundtrip, transiciones, history,
  dedup, interrupted, migración sin thumbnail, retención, concurrencia).
- Diseño completo en `sqlite-schema.md`.

### Fixed

- **uvicorn `--windowed`**: el formatter de logging `default` fallaba cuando no hay stdout
  (`Unable to configure formatter 'default'`). El launcher pasa un `log_config` mínimo.
- **Server error capture**: si uvicorn crashea en el thread daemon, el MessageBox ahora
  muestra el error real en vez de un mensaje genérico de firewall.
- **mypy cross-platform**: los imports condicionales de `webview` se ignoran vía
  `[[tool.mypy.overrides]]` en vez de inline `type: ignore` (roto en CI).
- `dist/` y `build/` excluidos de mypy para no escanear el output de PyInstaller.

## [1.6.0] — 2026-06-21

### Added

- Mensajes de error humanos: yt-dlp 403 / video privado / bloqueo regional / red / ffmpeg
  se traducen a texto entendible (antes se mostraba el error técnico crudo)
- Botón "Reintentar" en el error de descarga: re-dispara el job sin re-pegar la URL
- Thumbnail en el historial (`add_history_entry` ahora guarda `thumbnail`; entradas
  viejas sin el campo muestran sin imagen, sin romper)
- Tests del mapeo de errores

## [1.5.0] — 2026-06-21

### Added

- **mypy strict type checking** (`--strict`) with zero errors on all source files
- CI typecheck job in `.github/workflows/test.yml`
- `pydantic.mypy` plugin enabled for model validation

### Changed

- All route handlers and internal functions annotated with return types
- Generic type arguments added to `dict`, `set`, `list` declarations
- `import yt_dlp` annotated with `# type: ignore[import-untyped]`

## [1.4.0] — 2026-06-21

### Changed

- **`asyncio.Event` moved from `Job` model to `AppState.job_events`** — `Job` is now a pure Pydantic model without `arbitrary_types_allowed`
- **`_running_tasks` moved from module-level global to `AppState.running_tasks`** — last piece of mutable global state eliminated
- **Logo updated** from "ytgrab" to "OpenGrab" in the web UI
- **README badge** bumped to 1.4.0; installer section removed; file tree and test count updated

### Removed

- `install.py` — deprecated interactive installer; superseded by README + docker-compose

### Added

- 8 tests for `_run_download` in new `tests/test_download.py` (61 tests total): video success, audio success, fallback glob, extract_info None, no files, file not found, size enforcement, hook percent

### Fixed

- Dead CSS rule `.meta.hide` removed from `style.css`

## [1.3.0] — 2026-06-21

### Changed

- **yt-dlp pinned to an exact version** (`==2026.6.9`) in `requirements.txt` for reproducible image builds, replacing the unpinned `>=2025.1` floor
- **`OPENGRAB_AUTOUPDATE` now defaults to `0` (off)** — pulling the latest yt-dlp from PyPI unpinned on every start is a supply-chain risk; the secure/reproducible path is now the default. Opt in with `=1` when you need the newest fix immediately
- Dependabot checks pip daily (grouped for yt-dlp) so the pin stays current via reviewed PRs instead of runtime pulls

### Added

- `OPENGRAB_YTDLP_VERSION` — when auto-update is enabled, install this exact version instead of latest (reproducible updates on your terms)
- `OPENGRAB_MAX_TOTAL_MB` — disk budget for the download directory; new jobs are refused with HTTP 507 once current usage exceeds it
- Hard per-file size enforcement after download: if the final file exceeds `OPENGRAB_MAX_SIZE_MB` it is deleted and the job fails, covering the cases where yt-dlp's `filesize_approx` filter underestimated or did not apply (audio)
- `AppState.current_usage_bytes()` storage accounting helper
- Tests: total-disk-budget refusal (507), per-file size enforcement, usage accounting, config defaults

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
