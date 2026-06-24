# OpenGrab API Reference

**Base URL:** `/api`

Authentication is required on all `/api/*` endpoints unless `OPENGRAB_NO_AUTH=1`. Authenticate via:

- `Authorization: Bearer <token>` header
- `?token=<token>` query parameter
- `opengrab_token` HTTP-only cookie (set by `POST /api/auth`, 30-day expiry)

## Jobs

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/api/jobs` | 5/min | Create a download job |
| `GET` | `/api/jobs` | — | List active jobs |
| `GET` | `/api/jobs/{job_id}` | — | Job detail + progress |
| `GET` | `/api/jobs/{id}/events` | — | SSE progress stream |
| `GET` | `/api/jobs/{id}/file` | — | Download the completed file |
| `POST` | `/api/jobs/{id}/open-folder` | — | Open system file explorer |
| `DELETE` | `/api/jobs/{job_id}` | — | Cancel a job |

### `POST /api/jobs`

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
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
{"status": "downloading", "percent": 45.3, "speed": "12.3MiB/s", "eta": "00:42", "note": "", "filename": "", "error": ""}
```

On completion: `"status": "done"` with `"filename"` populated.
On error: `"status": "error"` with `"error"` containing the human-readable message.

## Info & Playlist

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/info?url=...` | 10/min | Fetch video metadata + formats |
| `GET` | `/api/playlist?url=...` | 10/min | List playlist entries |
| `POST` | `/api/playlist/download` | 2/min | Enqueue up to 100 playlist URLs |
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
| `POST` | `/api/channels` | 10/min | Add channel |
| `PUT` | `/api/channels/{id}` | 10/min | Update channel |
| `DELETE` | `/api/channels/{id}` | 10/min | Delete channel |
| `POST` | `/api/channels/{id}/check` | 5/min | Force check for new videos |

## Settings

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `GET` | `/api/settings` | — | Catalog with current values and metadata |
| `PUT` | `/api/settings` | 10/min | Update unlocked settings |

## Engine & Metrics

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/api/engine/update` | 2/min | Hot-swap yt-dlp from PyPI |
| `GET` | `/api/metrics` | — | Runtime metrics (auth-gated) |

## Auth

| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/api/auth` | — | Authenticate — body: `{"token": "..."}` |
| `POST` | `/api/logout` | — | Clear auth cookie |
| `GET` | `/health` | — | Health check (public) |

---

## Español

**URL base:** `/api`

Todas las rutas `/api/*` requieren autenticación salvo que `OPENGRAB_NO_AUTH=1`. Autenticación vía:

- Header `Authorization: Bearer <token>`
- Query param `?token=<token>`
- Cookie HTTP-only `opengrab_token` (30 días, seteada por `POST /api/auth`)

### Jobs

- `POST /api/jobs` — Crear job de descarga. Body: `{"url":"...", "quality":"best"}`
- `GET /api/jobs/{id}/events` — Stream SSE de progreso en tiempo real
- `GET /api/jobs/{id}/file` — Descargar el archivo final

### Info y Playlist

- `GET /api/info?url=...` — Metadata del video + formatos disponibles
- `GET /api/playlist?url=...` — Listar entradas de playlist
- `POST /api/playlist/download` — Encolar hasta 100 URLs

### Historial y Almacenamiento

- `GET /api/history` — Historial de descargas
- `DELETE /api/history` — Limpiar todo el historial
- `GET /api/storage` — Uso de disco, desglose por workdir

### Canales (Watch Mode)

- `GET/POST /api/channels` — Listar / agregar canal
- `PUT/DELETE /api/channels/{id}` — Modificar / eliminar
- `POST /api/channels/{id}/check` — Chequeo manual

### Configuración, Motor, Métricas

- `GET/PUT /api/settings` — Leer / modificar configuración
- `POST /api/engine/update` — Actualizar yt-dlp
- `GET /api/metrics` — Métricas de runtime
