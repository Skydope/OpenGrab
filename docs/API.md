# OpenGrab API Reference

**Base URL:** `/api`

Authentication is required on all `/api/*` endpoints (except `POST /api/auth`) unless `OPENGRAB_NO_AUTH=1`. Authenticate via:

- `Authorization: Bearer <token>` header
- `?token=<token>` query parameter
- `opengrab_token` HTTP-only cookie (set by `POST /api/auth`, 30-day expiry)

Rate limits are per client IP. Exceeding one returns `429`.

## Jobs

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/api/jobs` | 5/min | Create a download job |
| `GET` | `/api/jobs` | — | List live jobs in memory (used to rehydrate the UI on reopen) |
| `GET` | `/api/jobs/{id}/events` | — | SSE progress stream |
| `GET` | `/api/jobs/{id}/file` | — | Download the completed file |
| `GET` | `/api/jobs/{id}/extras` | — | List extra files in the job workdir (subs, thumbnail, info JSON) |
| `GET` | `/api/jobs/{id}/file/{filename}` | — | Download one of those extra files |
| `POST` | `/api/jobs/{id}/cancel` | — | Cancel a queued or running job — `404` if there is nothing to cancel |
| `POST` | `/api/jobs/{id}/dismiss` | — | Remove a finished job from the session view |
| `POST` | `/api/jobs/{id}/open-folder` | — | Open the system file explorer at the file (desktop only) |
| `POST` | `/api/jobs/{id}/move` | — | Move a `done` file to another folder — body: `{"dest": "<path>"}` (desktop only) |

### `POST /api/jobs`

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "quality": "best",
  "subs": false,
  "thumb": false,
  "infojson": false,
  "incognito": false,
  "incognito_dir": null,
  "clip_start": null,
  "clip_end": null,
  "clip_precise": true
}
```

`quality` must be one of: `best`, `1080p`, `720p`, `480p`, `audio` (`worst` is also accepted).

Optional flags: `subs`, `thumb`, `infojson` write the matching sidecar files.

`clip_start` / `clip_end` (float, seconds) download only that time range
("Clips" feature). Both must be provided or both omitted (`400` otherwise);
the range must satisfy `0 <= start < end` with a minimum length of 1 second.
Cutting uses yt-dlp's native `download_ranges` — on HLS/DASH extractors only
the fragments inside the range are fetched. `clip_precise` (default `true`)
chooses an exact cut (the segment is re-encoded, cost proportional to the clip)
or, when `false`, a faster keyframe-aligned cut without re-encoding. It only
applies to video: with `quality=audio` the mp3 contains only the range either way.
The filename gets a `[MM.SS-MM.SS]` suffix so two clips of the same video never
collide. If `clip_end` exceeds the real duration yt-dlp clamps to the end.

`incognito` (bool) runs the download without touching history or dedup: the file
is delivered to `incognito_dir` (an absolute folder path, **required** when
`incognito=true` → `400` otherwise), the temp workdir is force-wiped, yt-dlp
caching is disabled, and the DB row is deleted on completion/cancel/error. In
incognito the sidecar flags are ignored (single clean file). See
[SECURITY.md](SECURITY.md#incognito-mode--threat-model--limitations) for the
threat model and limitations (non-forensic wipe, DNS leak, server-mode path scope).

**Response:**
```json
{"job_id": "a1b2c3d4e5f6"}
```

### `GET /api/jobs/{id}/events`

SSE stream. Each event is a JSON snapshot:

```json
{"status": "downloading", "percent": 45.3, "speed": "12.3MiB/s", "eta": "00:42", "note": "", "filename": "", "error": ""}
```

On completion: `"status": "done"` with `"filename"` populated.
On error: `"status": "error"` with `"error"` containing the human-readable message.

## Info & Playlist

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/info?url=...` | 10/min | Fetch video metadata + formats |
| `GET` | `/api/playlist?url=...` | 10/min | List playlist entries |
| `POST` | `/api/playlist/download` | 2/min | Enqueue up to 100 playlist URLs — body: `{"urls": [...], "quality": "best"}` |
| `GET` | `/api/jobs/batch-status?ids=...` | — | Batch status (comma-separated IDs) |

## History & Storage

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/history?limit=20` | — | Download history |
| `DELETE` | `/api/history/{job_id}` | — | Delete single entry (secure wipe in background) |
| `DELETE` | `/api/history` | 5/min | Clear all history |
| `GET` | `/api/storage` | — | Usage breakdown, workdir sizes, DB size |
| `POST` | `/api/storage/cleanup` | 5/min | Delete old workdirs — body: `{"max_age_hours": 24}` |
| `POST` | `/api/storage/cleanup-all` | 3/min | Delete all workdirs |

## Channels (Watch Mode)

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/channels` | — | List all channels |
| `POST` | `/api/channels` | 10/min | Add channel — body: `{"url": "...", "quality": "best", "interval_minutes": 60}` |
| `PUT` | `/api/channels/{id}` | 10/min | Update channel (title, quality, interval, enabled) |
| `DELETE` | `/api/channels/{id}` | 10/min | Delete channel |
| `POST` | `/api/channels/{id}/check` | 5/min | Force check for new videos |

## Settings

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/settings` | — | Catalog with current values and metadata |
| `GET` | `/api/settings/defaults` | — | Defaults the frontend needs to initialize its controls |
| `PUT` / `PATCH` | `/api/settings` | 10/min | Update settings — body: `{"max_jobs": 3, ...}`. Keys locked by env return `400` |

## Backup

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/backup/export` | 5/min | Export settings, history and channels as JSON |
| `POST` | `/api/backup/import` | 2/min | Import a backup (`"version": 1`). Merges settings (env/ini locks respected), inserts history as `done`, imports channels disabled |

## Engine & Metrics

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/api/engine/update` | 2/min | Hot-swap yt-dlp from PyPI |
| `GET` | `/api/metrics` | — | Runtime metrics as JSON (version, uptime, job counts, disk usage) |
| `GET` | `/metrics` | — | Prometheus metrics (public, no auth — see [OBSERVABILITY.md](OBSERVABILITY.md)) |

## Auth & System

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/api/auth` | — | Authenticate — body: `{"token": "..."}` |
| `POST` | `/api/logout` | — | Clear auth cookie |
| `GET` | `/health` | — | Health check (public) |
| `GET` | `/` | — | Web UI |
| `POST` | `/api/open-downloads-folder` | — | Open the downloads folder in the system file explorer (desktop only) |
| `GET` | `/api/debug/routes` | — | List every registered route and its methods |

Desktop-only endpoints return `409` in server mode.

---

## Español

**URL base:** `/api`

Todas las rutas `/api/*` (salvo `POST /api/auth`) requieren autenticación, a menos que `OPENGRAB_NO_AUTH=1`. Autenticación vía:

- Header `Authorization: Bearer <token>`
- Query param `?token=<token>`
- Cookie HTTP-only `opengrab_token` (30 días, la setea `POST /api/auth`)

Los límites de peticiones son por IP; al superarlos se responde `429`. Los detalles de cada cuerpo de petición están en la sección en inglés.

### Jobs

- `POST /api/jobs` — Crear un trabajo de descarga. Body: `{"url":"...", "quality":"best"}`. Flags opcionales: `subs`, `thumb`, `infojson`. Clips: `clip_start` + `clip_end` (segundos) y `clip_precise` (corte exacto o al keyframe). Modo incógnito: `incognito:true` + `incognito_dir` (carpeta destino obligatoria) — sin historial, wipe del temp, fila de DB borrada al terminar. Ver [SECURITY.md](SECURITY.md#modo-incógnito--modelo-de-amenaza-y-límites).
- `GET /api/jobs` — Listar los trabajos vivos en memoria
- `GET /api/jobs/{id}/events` — Stream SSE de progreso en tiempo real
- `GET /api/jobs/{id}/file` — Descargar el archivo final
- `GET /api/jobs/{id}/extras` y `GET /api/jobs/{id}/file/{filename}` — Listar y descargar archivos extra (subtítulos, miniatura, info JSON)
- `POST /api/jobs/{id}/cancel` — Cancelar un trabajo en cola o en curso
- `POST /api/jobs/{id}/dismiss` — Quitar un trabajo terminado de la vista
- `POST /api/jobs/{id}/open-folder` y `POST /api/jobs/{id}/move` — Abrir la carpeta del archivo / moverlo a otra carpeta (solo escritorio)

### Info y Playlist

- `GET /api/info?url=...` — Metadata del video + formatos disponibles
- `GET /api/playlist?url=...` — Listar entradas de playlist
- `POST /api/playlist/download` — Encolar hasta 100 URLs
- `GET /api/jobs/batch-status?ids=...` — Estado de varios trabajos de un lote

### Historial y Almacenamiento

- `GET /api/history` — Historial de descargas
- `DELETE /api/history/{job_id}` — Borrar una entrada (wipe seguro en segundo plano)
- `DELETE /api/history` — Limpiar todo el historial
- `GET /api/storage` — Uso de disco, desglose por workdir
- `POST /api/storage/cleanup` y `POST /api/storage/cleanup-all` — Borrar workdirs viejos / todos

### Canales (Watch Mode)

- `GET/POST /api/channels` — Listar / agregar canal
- `PUT/DELETE /api/channels/{id}` — Modificar / eliminar
- `POST /api/channels/{id}/check` — Chequeo manual

### Configuración y Backup

- `GET /api/settings` y `GET /api/settings/defaults` — Leer la configuración / los valores por defecto
- `PUT` o `PATCH /api/settings` — Modificar la configuración
- `GET /api/backup/export` y `POST /api/backup/import` — Exportar / importar settings, historial y canales en JSON

### Motor, Métricas y Sistema

- `POST /api/engine/update` — Actualizar yt-dlp
- `GET /api/metrics` — Métricas de runtime en JSON
- `GET /metrics` — Métricas Prometheus (pública, sin auth)
- `POST /api/auth` y `POST /api/logout` — Iniciar / cerrar sesión
- `GET /health` — Chequeo de salud (público)
- `POST /api/open-downloads-folder` — Abrir la carpeta de descargas (solo escritorio)
- `GET /api/debug/routes` — Listar todas las rutas registradas
