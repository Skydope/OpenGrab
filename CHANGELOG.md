# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- **New `docs/` folder** with professional, bilingual documentation:
  - `docs/INSTALL.md` — Docker, Desktop, and Bare Metal installation guides
  - `docs/DEPLOY.md` — Production deployment with nginx, systemd, and Docker Compose
  - `docs/API.md` — Complete API reference with request/response examples
  - `docs/SECURITY.md` — Security policy covering SSRF defense, auth, and reporting
  - `docs/CONTRIBUTING.md` — Contribution guidelines and quality standards
  - `docs/examples/opengrab.service` — Example systemd unit file
- **README.md** modernized with Highlights, Quick Start, and Documentation sections at the top, plus updated badges and Spanish section
- **CHANGELOG.md** now links to docs/ for detailed documentation

## [1.10.0] — 2026-06-23

### Security

- **SSRF por DNS cerrado (capa 1).** El gate `_is_safe_url` ahora resuelve DNS
  (`getaddrinfo` con `AF_UNSPEC`) y valida todas las IPs (A + AAAA) contra las
  reglas de bloqueo. Antes solo chequeaba IPs literales en la URL; un dominio
  con registro A apuntando a `169.254.169.254` o `10.0.0.5` pasaba derecho porque
  `ipaddress.ip_address()` lanzaba `ValueError` y el gate retornaba `True`.
  Ahora se resuelve y se bloquea si cualquiera de las IPs cae en rangos privados,
  loopback, link-local (metadata cloud), reservados, multicast o unspecified.
  Política **strict on DNS failure**: si la resolución falla, bloquea con mensaje
  "no se pudo resolver el host" (distinto de "resuelve a IP privada").
- **Egress firewall en DOCKER-USER (capa 2).** Script `scripts/egress-lockdown.sh`
  que inserta reglas DROP en la chain `DOCKER-USER` del host, scopeadas por la
  subnet del contenedor, sin afectar otros contenedores del homelab. Cierra el
  TOCTOU/DNS-rebinding residual entre la resolución de capa 1 y la conexión real
  de yt-dlp. Sin `127.0.0.0/8` (respeta el DNS embebido de Docker en
  `127.0.0.11`). Idempotente (`-C` check), con `--dry-run`, `--log` y `--remove`.

### Added

- `scripts/egress-lockdown.sh`: firewall egress scopeado por contenedor.
- 14 tests de loops de fondo en `tests/test_state.py`: 8 de `evict_once`, 1 de
  `evict_loop`, 5 de `watch_loop` (dispatch, dedup por downloaded/active/interval,
  error handling). Mismo patrón que `test_dispatch.py`.

### Changed

- `_is_safe_url` ahora devuelve `tuple[bool, str]`; los 4 endpoints que la llaman
  propagan el `reason` al usuario en vez de un mensaje hardcodeado.
- CI: el job `typecheck` ahora matrixea Python `["3.12", "3.13", "3.14"]` (antes
  pineaba solo 3.13). Errores de mypy específicos de 3.14 ya no se escapan a CI.

## [1.9.0] — 2026-06-23

### Added

- **Panel de historial**: lista de descargas completadas (con thumbnail), borrado
  de entradas individuales (`DELETE /api/history/{id}`) y limpieza total
  (`DELETE /api/history`). El borrado de la fila en SQLite es síncrono e
  instantáneo; el borrado seguro del archivo y del workdir corre en background
  para no bloquear la respuesta.
- **Gestión de almacenamiento**: `GET /api/storage` (uso total, desglose por
  workdir, archivos sueltos y tamaño de la DB), `POST /api/storage/cleanup`
  (por antigüedad, con `dry_run`) y `POST /api/storage/cleanup-all`.
- **Descarga de playlist por lotes**: `GET /api/playlist` lista los videos de una
  playlist (`extract_flat`), `POST /api/playlist/download` encola hasta 100 URLs
  (cada una pasa por el gate SSRF-safe), `GET /api/jobs/batch-status` devuelve el
  estado combinado (memoria + DB) y un **dispatch loop** despacha la cola en
  background. La UI agrega selección de videos y polling de progreso por lote.

### Fixed

- **Descarga fantasma de jobs fallidos**: un job que fallaba solo mutaba el estado
  en memoria; en SQLite quedaba `queued` y, con el dispatch loop nuevo, se
  re-despachaba tras `evict_once` (~1h). Ahora `_run_download` persiste
  `status='error'` en la DB.
- **El batch podía exceder `MAX_JOBS`**: el dispatch loop despachaba `MAX_JOBS`
  jobs por tick sin contar las descargas ya activas (manuales o de un batch
  previo). Ahora descuenta `count_active_jobs()`, tratando `MAX_JOBS` como techo
  de concurrencia real.

### Changed

- Mensajes de error del path de playlist traducidos al español (antes mezclaban
  inglés con el resto de la UI).

### Internal

- Test de `dispatch_loop` (`marks_starting`) corregido: pasaba en vacío porque el
  fake era async sobre un `asyncio.to_thread` (callable síncrono); ahora el fake
  es síncrono, se drenan las tasks y hay un guard anti-vacuo.
- El docstring del secure-delete documenta sus límites en SSD/CoW (no es borrado
  forense garantizado fuera de HDD magnético).

## [1.8.0] — 2026-06-22

### Added

- **Modo universal**: el gate de validacion de URL paso de un allowlist de 5
  plataformas (YouTube, Vimeo, TikTok, X, Instagram) a `_is_safe_url`, que acepta
  cualquier http(s) publico y deja que yt-dlp decida si puede extraer. OpenGrab
  ahora funciona con los ~1800 sitios que soporta yt-dlp (Bandcamp, SoundCloud,
  etc.), no solo los 5 originales.
- Defensa en profundidad **anti-SSRF** en el nuevo gate: rechaza esquemas no-http
  (file://, ftp://, javascript:), localhost, `.local`, IPs internas
  (privada/loopback/link-local/reservada/multicast/unspecified) y el endpoint de
  metadata cloud (`169.254.169.254`).
- **Indicador de sitio detectado** en la UI (`extractor_key`): muestra "Sitio
  detectado: YouTube" (o el extractor que corresponda) al analizar una URL.
- **Retries** en las opciones de descarga (`extractor_retries=3`,
  `fragment_retries=5`, `retries=5`) para robustez ante extractors o redes
  fragiles.
- `has_active_job_for_video` en la capa SQLite para prevenir jobs duplicados
  entre ciclos del watch loop.

### Changed

- Mensajes de error de URL ahora universales, sin listar plataformas especificas.
- `_looks_like_supported` renombrado a `_is_safe_url` (la semantica ahora es
  "es seguro pasar esto a yt-dlp", no "coincide con nuestro allowlist").

### Fixed

- **Watch mode no descargaba nada**: `_check_channel_watch` creaba jobs pero nunca
  disparaba `_run_download`. Ahora retorna la lista de videos y `watch_loop` los
  despacha como tareas asyncio (igual que `api_create_job`).
- **Dedup llamaba `record_download` al crear el job, no al completar**: descargas
  fallidas quedaban marcadas como bajadas y nunca se reintentaban. Ahora
  `record_download` se llama en el camino de exito de `_run_download`, cubriendo
  tanto watch mode como descargas manuales.
- DeprecationWarning de `asyncio.iscoroutinefunction` (slowapi + Python 3.14+):
  monkeypatch a `inspect.iscoroutinefunction` al arranque.

### Security

- Gate SSRF-safe: defensa en profundidad contra requests del lado del servidor a
  destinos internos (ver Added).

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

### Added (SQLite — PR-0, data layer)

- `db.py`: capa de acceso SQLite (conexión WAL + lock serializado). Tabla única `jobs`
  (cola + historial), `channels` y `downloaded_urls` para watch mode. CRUD de jobs,
  transiciones, `mark_interrupted()` (devuelve workdirs para limpiar), dedup
  (`record_download`/`is_downloaded`), `prune_history`, e import del `history.json` legacy.
- `tests/test_db.py`: 17 tests en `:memory:`/temp (roundtrip, transiciones, history,
  dedup, interrupted, migración sin thumbnail, retención, concurrencia).
- Diseño completo en `sqlite-schema.md`.

### Added (SQLite — PR-1, cableado a AppState)

- **`state.py`**: AppState ahora recibe `Database` por inyección. Removido historial
  JSON (`load_history`, `_write_history`, `add_history_entry`). Nuevos métodos
  `complete_job()` (persiste transición en DB) y `get_history()` (con alias
  `job_id` para el frontend). `evict_once` llama `prune_history`.
- **`download.py`**: `add_history_entry({...})` → `complete_job(job_id, ...)`.
- **`routes.py`**: `api_create_job` inserta en DB; `api_history` lee de `get_history()`.
- **`app.py` lifespan**: crea `Database`, `mark_interrupted` (crash recovery + limpia
  workdirs), `import_history_json` (migración one-shot del JSON legacy), `prune_history`,
  `db.close()` en shutdown.
- **Migración**: el `history.json` legacy se importa una sola vez (idempotente). El
  archivo queda intacto por si se necesita downgradear.

### Added (Watch mode — canales)

- **Channel CRUD**: `db.py` con 6 métodos para insert/update/delete/get/list/touch de
  canales. Column whitelist anti-injection. Tablas `channels` y `downloaded_urls` que
  ya existían (schema v1) ahora tienen API pública completa.
- **`_check_channel_watch`**: lógica de chequeo en `download.py`. Usa `extract_flat=True`
  (rápido, sin descargar), filtra contra `downloaded_urls` (dedup), y crea jobs para
  los videos nuevos.
- **6 endpoints REST**: `GET/POST /api/channels`, `PUT/DELETE /api/channels/{id}`,
  `POST /api/channels/{id}/check` (chequeo manual). Rate limits: 10/min y 5/min.
- **`watch_loop` scheduler**: corre cada 60s en `AppState`, respeta el intervalo por
  canal (`interval_minutes`), ejecuta el chequeo en thread pool vía `asyncio.to_thread`.
  Arranca desde el lifespan junto al eviction loop.
- **UI "Canales"**: sección nueva debajo de Historial. Agregar canal (URL + calidad +
  intervalo), toggle enable/disable, chequeo manual, borrar. Alpine.js + CSS.

### Added (Polish)

- **Graceful shutdown**: `atexit.register(self.db.close)` en `AppState.__init__`.
  Asegura que la DB se cierra al salir del proceso (Docker, bare metal o desktop).
- **Dependencias unificadas**: `requirements.txt` eliminado. Las dependencias de runtime
  (fastapi, uvicorn, yt-dlp, slowapi) ahora viven en `pyproject.toml [project] dependencies`.
  Dockerfile y CI actualizados a `pip install -e .`.
- **README**: actualizado de v1.6.0 a v1.7.0 — features nuevas, tech stack ampliado,
  variables de entorno completas (12 vars), API reference corregida (13 endpoints),
  sección Desktop App, file tree actualizado, badge de versión.
- **sqlite-schema.md**: documento de diseño del schema SQLite (tablas, flujo de datos,
  concurrencia, crash recovery, migración).
- **`.env.example`**: agregados `OPENGRAB_TRUST_XFF` y `OPENGRAB_CONFIG`.

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

[1.9.0]: https://github.com/skydope/opengrab/releases/tag/v1.9.0
[1.8.0]: https://github.com/skydope/opengrab/releases/tag/v1.8.0
[1.2.0]: https://github.com/skydope/opengrab/releases/tag/v1.2.0
[1.1.0]: https://github.com/skydope/opengrab/releases/tag/v1.1.0
[1.0.0]: https://github.com/skydope/opengrab/releases/tag/v1.0.0
