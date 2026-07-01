# SQLite Schema — OpenGrab

Diseño de la capa de persistencia para jobs, historial, watch mode y settings. Versión 4 del schema.

## Principios

- **RAM para progreso, DB para transiciones.** percent/speed/eta viven en `AppState.jobs` (dict en memoria) para SSE en tiempo real. Solo cambios de estado (queued → downloading → done) tocan SQLite. Sin escrituras por tick de progreso.
- **Tabla única `jobs`** oficia de cola + historial. "Historial" = `SELECT * FROM jobs WHERE status='done' ORDER BY completed DESC`.
- **WAL mode** para concurrencia entre el event loop (lecturas) y el thread pool de descargas (escrituras).
- **Lock serializado.** Un solo `threading.Lock` protege toda la conexión. Las operaciones son infrecuentes (transiciones, no ticks) y rápidas → no es cuello de botella.
- **Column whitelist.** `update_job` solo acepta columnas en `_UPDATABLE`. Defensa en profundidad — incluso con queries parametrizadas, evita inyección de nombres de columna.

---

## Schema v1

### `jobs` — cola + historial

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,       -- uuid hex[:12]
    url         TEXT NOT NULL,          -- URL original
    quality     TEXT NOT NULL,          -- best | 1080p | 720p | 480p | audio
    status      TEXT NOT NULL,          -- queued | starting | downloading | processing | done | error | interrupted | cancelled
    title       TEXT,                   -- título del video (safe_name aplicado)
    filename    TEXT,                   -- nombre de archivo final (Title.ext)
    filepath    TEXT,                   -- ruta absoluta al archivo
    mime        TEXT,                   -- video/mp4 | audio/mpeg
    size        INTEGER,                -- bytes del archivo final
    thumbnail   TEXT,                   -- URL de thumbnail
    error       TEXT,                   -- mensaje de error (mapeado a humano)
    video_id    TEXT,                   -- ID nativo de la plataforma (poblar en watch mode)
    extractor   TEXT,                   -- youtube | vimeo | tiktok | ... (poblar en watch mode)
    workdir     TEXT,                   -- tempdir opengrab_* para limpieza post-crash
    created     REAL NOT NULL,          -- timestamp UNIX de creación
    completed   INTEGER,                -- timestamp UNIX de finalización
    incognito   INTEGER NOT NULL DEFAULT 0,  -- 1 = descarga incógnito (v3); la fila se borra al terminar
    playlist_subdir TEXT                -- v4: nombre de subcarpeta (sanitizado) si el job
                                         -- vino de una descarga de playlist con "guardar en
                                         -- subcarpeta" activado; NULL = comportamiento normal
);

CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created);
```

> **Migración de columnas (v4):** `CREATE TABLE IF NOT EXISTS` no le agrega
> columnas a una tabla `jobs` que ya existía en disco de una versión previa.
> `_migrate(from_version)` corre `ALTER TABLE jobs ADD COLUMN` guardado por
> `PRAGMA table_info(jobs)` (idempotente) para cada columna agregada
> post-lanzamiento — `incognito` en v3, `playlist_subdir` en v4. Cualquier
> columna nueva futura debe seguir el mismo patrón: un bloque
> `if from_version < N and "col" not in cols: ALTER TABLE ...` en `_migrate`.

**Sobre `incognito` (v3):** una descarga en modo incógnito inserta su fila
normalmente (para ocupar slot y sobrevivir dentro del proceso), pero al llegar a
cualquier estado terminal (`done`/`cancelled`/`error`) la fila se **borra** en
vez de persistirse — nunca aparece en historial ni en `downloaded_urls`. No se
persiste la carpeta destino (`incognito_dir`) porque sería un rastro. Por eso un
job incógnito **no se auto-reanuda** tras un reinicio: `get_queued` lo excluye y
`reconcile_startup` borra su fila y devuelve el `workdir` para secure-wipe.

**Sobre `playlist_subdir` (v4):** lo setea `/api/playlist/download` cuando el
usuario tilda "guardar en subcarpeta" — mismo valor sanitizado (`_safe_name`)
para todos los jobs de ese batch. `_run_download` lo antepone al destino final
(`out_dir/<subdir>/` en server mode, `library_dir/<subdir>/<name_template>` en
desktop). Hoy no interactúa con `incognito`: `BatchReq` (endpoint de playlist)
no tiene campo `incognito`, y `JobReq` (endpoint de job individual, el único
que puede setear `incognito=True`) no tiene `playlist_subdir` — son caminos
disjuntos. Si en el futuro se unifican, falta definir precedencia.

**Estados y transiciones:**

```
queued → starting → downloading → processing → done
  ↓                                         ↓
  └────────────── error ────────────────────┘

(reinicio tras crash)
queued | starting | downloading | processing → interrupted
```

**Columnas que NO van a la DB (solo RAM):**
- `percent`, `speed`, `eta`, `downloaded`, `total`, `note` — progreso en vivo, efímero.

### `channels` — watch mode (diseñado, sin poblar)

```sql
CREATE TABLE IF NOT EXISTS channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url              TEXT NOT NULL UNIQUE,   -- URL del canal/playlist
    title            TEXT,
    quality          TEXT NOT NULL DEFAULT 'best',
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_checked     INTEGER,
    created          INTEGER NOT NULL
);
```

### `downloaded_urls` — dedup para watch mode (diseñado, sin poblar)

```sql
CREATE TABLE IF NOT EXISTS downloaded_urls (
    extractor     TEXT NOT NULL,
    video_id      TEXT NOT NULL,
    channel_id    INTEGER REFERENCES channels(id),
    job_id        TEXT REFERENCES jobs(id),
    downloaded_at INTEGER NOT NULL,
    PRIMARY KEY (extractor, video_id)
);
```

### `settings` — runtime settings (persistidos en DB)

```sql
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated INTEGER NOT NULL
);
```

---

## API pública (`Database`)

### CRUD de jobs

| Método | Qué hace | Cuándo se llama |
|--------|----------|-----------------|
| `insert_job(id, url, quality)` | Inserta fila con status=`queued` | `POST /api/jobs` |
| `update_job(id, **fields)` | Actualiza columnas whitelisteadas | Transiciones de estado |
| `get_job(id)` | Devuelve fila completa o `None` | Debug, no usado en hot path |
| `get_active_jobs()` | Jobs en `ACTIVE_STATUSES`, orden creación | Crash recovery |
| `get_history(limit)` | Jobs `done`, más recientes primero | `GET /api/history` |

### Crash recovery

| Método | Qué hace |
|--------|----------|
| `mark_interrupted()` | Flips `queued/starting/downloading/processing` → `interrupted`. Devuelve workdirs afectados para limpiar en filesystem. |

Se llama en el lifespan al arrancar. Si el proceso anterior murió con jobs activos, quedan marcados `interrupted` y sus tempdirs se borran.

### Retención

| Método | Qué hace |
|--------|----------|
| `prune_history(keep)` | Borra jobs `done` excepto los `keep` más recientes. `keep<=0` = no-op. |

Se llama desde `AppState.evict_once()` cada 5 minutos con `keep=HISTORY_MAX` (500).

### Dedup (watch mode, API lista, sin cablear)

| Método | Qué hace |
|--------|----------|
| `record_download(extractor, video_id, job_id)` | `INSERT OR IGNORE` en `downloaded_urls` |
| `is_downloaded(extractor, video_id)` | `SELECT 1` → bool |

---

## Flujo de datos

```
┌─────────────────────────────────────────────────────────┐
│                     RAM (AppState)                       │
│  jobs: dict[id, Job]  ← percent, speed, eta, status     │
│  job_events: dict[id, Event]  ← SSE wakeups             │
│  running_tasks: set[Task]  ← GC protection              │
├─────────────────────────────────────────────────────────┤
│                   SQLite (Database)                      │
│  jobs table  ← url, quality, status, title, filepath,   │
│                mime, size, thumbnail, error, workdir     │
│  channels + downloaded_urls  ← watch mode (futuro)      │
└─────────────────────────────────────────────────────────┘

Crear job:
  RAM: Job(id) + Event  →  DB: insert_job(id, url, quality, status=queued)

Progreso:
  RAM: hook → job.percent/speed/eta  →  evt.set()  →  SSE
  DB:  (sin cambios)

Completar:
  RAM: job.status="done", filepath, filename, mime, title
  DB:  complete_job(id, status="done", title, filepath, mime, size, thumbnail, completed)

Historial:
  GET /api/history  →  AppState.get_history(limit)  →  DB.get_history(limit)
  SELECT * FROM jobs WHERE status='done' ORDER BY completed DESC LIMIT ?
  Alias: job_id = id (compat con frontend Alpine.js)

Eviction (cada 5 min):
  RAM: borra jobs done/error >1h, limpia workdirs
  DB:  prune_history(keep=500)

Crash recovery (startup):
  DB:  mark_interrupted() → flips activos → interrupted
  FS:  shutil.rmtree(workdir) para cada afectado
```

---

## Concurrencia

```
                  ┌──────────────┐
                  │  Event Loop  │  GET /api/history → db.get_history()
                  │  (asyncio)   │  POST /api/jobs  → db.insert_job()
                  └──────┬───────┘
                         │
                  ┌──────┴───────┐
                  │  Thread Pool │  _run_download → state.complete_job()
                  │  (yt-dlp)    │                 → db.update_job()
                  └──────────────┘
                         │
                  ┌──────┴───────┐
                  │  threading   │  Serializa TODO acceso a sqlite3.Connection
                  │  .Lock       │
                  └──────────────┘
```

- `check_same_thread=False`: la misma conexión se comparte entre event loop y thread pool.
- El lock se adquiere en cada operación pública de `Database`. Como son infrecuentes (máx ~5/min en creación, ~1 por descarga), la contención es negligible.
- WAL permite lecturas concurrentes con escrituras sin bloquear.


