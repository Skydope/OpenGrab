"""Capa de acceso a SQLite para OpenGrab (Balde B, PR-0).

Diseño (ver sqlite-schema.md):
- Tabla única `jobs`: cola + historial. "history" = SELECT WHERE status='done'.
- Escrituras solo en transiciones de estado (NO en cada tick de progreso) → el progreso
  fino (percent/speed/eta) vive en RAM, no acá.
- `channels` / `downloaded_urls` + `video_id`/`extractor` en `jobs`: diseñados ahora para
  watch mode (dedup), poblados después.

Concurrencia: una conexión compartida (`check_same_thread=False`) + un `threading.Lock`
que serializa TODO acceso. El thread pool de descargas y el event loop comparten la misma
conexión; como las operaciones son infrecuentes (transiciones, no ticks) y rápidas, el lock
no es cuello de botella. WAL para durabilidad/perf.

Este módulo es path-agnóstico (recibe la ruta) y se testea contra ``:memory:``.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 5

# Estados que cuentan como "en curso" (concurrencia, dedup, conteos).
ACTIVE_STATUSES = ("queued", "starting", "downloading", "processing")

# Subconjunto de ACTIVE que implicaba un proceso/thread VIVO. Al reiniciar son
# huérfanos (el proceso murió): se marcan 'interrupted' y se limpia su workdir.
# 'queued' queda afuera a propósito: nunca arrancó, no tiene workdir → se retoma.
ORPHAN_STATUSES = ("starting", "downloading", "processing")

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    quality     TEXT NOT NULL,
    status      TEXT NOT NULL,
    title       TEXT,
    filename    TEXT,
    filepath    TEXT,
    mime        TEXT,
    size        INTEGER,
    thumbnail   TEXT,
    error       TEXT,
    video_id    TEXT,
    extractor   TEXT,
    workdir     TEXT,
    created     REAL NOT NULL,
    completed   INTEGER,
    incognito   INTEGER NOT NULL DEFAULT 0,
    playlist_subdir TEXT,
    subs        INTEGER NOT NULL DEFAULT 0,
    thumb       INTEGER NOT NULL DEFAULT 0,
    infojson    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created);

CREATE TABLE IF NOT EXISTS channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url              TEXT NOT NULL UNIQUE,
    title            TEXT,
    quality          TEXT NOT NULL DEFAULT 'best',
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_checked     INTEGER,
    created          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS downloaded_urls (
    extractor     TEXT NOT NULL,
    video_id      TEXT NOT NULL,
    channel_id    INTEGER REFERENCES channels(id),
    job_id        TEXT REFERENCES jobs(id),
    downloaded_at INTEGER NOT NULL,
    PRIMARY KEY (extractor, video_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated INTEGER NOT NULL
);
"""

# Columnas de `jobs` que se pueden actualizar vía update_job (whitelist anti-injection).
_UPDATABLE = frozenset(
    {
        "status", "title", "filename", "filepath", "mime", "size",
        "thumbnail", "error", "video_id", "extractor", "workdir", "completed",
    }
)


class Database:
    """Acceso serializado a la DB. Todas las operaciones toman el lock."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Schema / migración
    # ------------------------------------------------------------------ #
    def _init_schema(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version < SCHEMA_VERSION:
                self._conn.executescript(_DDL)
                self._migrate(version)
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._conn.commit()

    def _migrate(self, from_version: int) -> None:
        """Migraciones incrementales sobre tablas YA existentes.

        ``_DDL`` solo crea tablas/índices ausentes (``IF NOT EXISTS``); NO altera
        una tabla ``jobs`` que ya existía de una versión anterior. Las columnas
        nuevas se agregan acá con ``ALTER TABLE``, guardadas por ``PRAGMA
        table_info`` para ser idempotentes (no fallar si la columna ya está, p.ej.
        en una DB recién creada por ``_DDL``). Corre dentro del lock + la misma
        transacción que el bump de ``user_version``.
        """
        cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        # v3: descargas en modo incógnito (no persistir en historial).
        if from_version < 3 and "incognito" not in cols:
            self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN incognito INTEGER NOT NULL DEFAULT 0"
            )
        # v4: subcarpeta de destino para descargas de playlist agrupadas.
        if from_version < 4 and "playlist_subdir" not in cols:
            self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN playlist_subdir TEXT"
            )
        # v5: persistir opciones de sidecars (subs/thumb/infojson). Antes solo
        # vivian en el DownloadContext en memoria: un job 'queued' que
        # sobrevivia a un restart era re-despachado por dispatch_loop sin
        # ellas y las perdia en silencio.
        if from_version < 5:
            for col in ("subs", "thumb", "infojson"):
                if col not in cols:
                    self._conn.execute(
                        f"ALTER TABLE jobs ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                    )

    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Jobs
    # ------------------------------------------------------------------ #
    def insert_job(
        self, job_id: str, url: str, quality: str,
        status: str = "queued", created: float | None = None,
        workdir: str | None = None, incognito: bool = False,
        playlist_subdir: str | None = None,
        subs: bool = False, thumb: bool = False, infojson: bool = False,
    ) -> None:
        created = time.time() if created is None else created
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, url, quality, status, created, workdir, "
                "incognito, playlist_subdir, subs, thumb, infojson) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, url, quality, status, created, workdir, int(incognito),
                 playlist_subdir, int(subs), int(thumb), int(infojson)),
            )
            self._conn.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        """Actualiza columnas en una transición. Solo columnas whitelisteadas."""
        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"columnas no actualizables: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                # cols viene de _UPDATABLE (whitelist), valores parametrizados
                f"UPDATE jobs SET {cols} WHERE id=?",  # nosec B608
                (*fields.values(), job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_jobs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch multiple jobs by ID in a single query. Returns empty list if none found."""
        if not job_ids:
            return []
        placeholders = ", ".join("?" * len(job_ids))
        with self._lock:
            rows = self._conn.execute(
                # placeholders = '?,...' generado, ids parametrizados
                f"SELECT * FROM jobs WHERE id IN ({placeholders})",  # nosec B608
                job_ids,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_active_jobs(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with self._lock:
            rows = self._conn.execute(
                # placeholders de constante ACTIVE_STATUSES
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created",  # nosec B608
                ACTIVE_STATUSES,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Jobs completados, más recientes primero. limit<=0 = sin límite."""
        sql = "SELECT * FROM jobs WHERE status='done' ORDER BY completed DESC"
        params: tuple[Any, ...] = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def reconcile_startup(self) -> dict[str, list[Any]]:
        """Reconcilia jobs de un proceso anterior al arrancar (ver sqlite-schema.md §5.1).

        Distingue dos casos que antes se trataban igual (y perdían cola):

        - ``queued``: nunca arrancó. Sin workdir, sin subproceso, sin bytes
          parciales (el workdir se crea recién en ``_run_download``, tras pasar a
          ``starting``). Es trabajo pendiente, NO un huérfano → se deja en
          ``queued`` y el ``dispatch_loop`` lo retoma solo al boot.
        - ``starting``/``downloading``/``processing`` (ORPHAN_STATUSES): tenían un
          thread/subproceso vivo que ya no existe → huérfanos. Se marcan
          ``interrupted`` y el caller limpia su workdir parcial.

        Devuelve ``{"requeued": [ids], "interrupted": [{id, workdir}],
        "incognito_dropped": [{id, workdir}]}``.

        Los jobs ``incognito=1`` que sobrevivieron a un proceso anterior NO se
        reanudan ni se conservan: no persistimos su carpeta destino (sería un
        rastro), y re-despacharlos como descarga normal filtraría a historial.
        Se borran de la DB y se devuelve su workdir para que el caller haga el
        secure-wipe del residuo parcial.
        """
        orphan_ph = ", ".join("?" for _ in ORPHAN_STATUSES)
        with self._lock:
            incognito_rows = self._conn.execute(
                "SELECT id, workdir FROM jobs WHERE incognito=1"
            ).fetchall()
            incognito_dropped = [dict(r) for r in incognito_rows]
            if incognito_dropped:
                ids = [r["id"] for r in incognito_dropped]
                ph = ", ".join("?" for _ in ids)
                self._conn.execute(
                    # ph = '?,...' generado, ids parametrizados
                    f"DELETE FROM downloaded_urls WHERE job_id IN ({ph})", ids  # nosec B608
                )
                # ph = '?,...' generado, ids parametrizados
                self._conn.execute(f"DELETE FROM jobs WHERE id IN ({ph})", ids)  # nosec B608
            requeued = [
                r["id"]
                for r in self._conn.execute(
                    "SELECT id FROM jobs WHERE status='queued' ORDER BY created"
                ).fetchall()
            ]
            orphan_rows = self._conn.execute(
                # orphan_ph de constante ORPHAN_STATUSES
                f"SELECT id, workdir FROM jobs WHERE status IN ({orphan_ph})",  # nosec B608
                ORPHAN_STATUSES,
            ).fetchall()
            interrupted = [dict(r) for r in orphan_rows]
            if interrupted:
                self._conn.execute(
                    # orphan_ph de constante ORPHAN_STATUSES
                    f"UPDATE jobs SET status='interrupted' "  # nosec B608
                    f"WHERE status IN ({orphan_ph})",
                    ORPHAN_STATUSES,
                )
            self._conn.commit()
        return {
            "requeued": requeued,
            "interrupted": interrupted,
            "incognito_dropped": incognito_dropped,
        }

    def prune_history(self, keep: int) -> int:
        """Conserva los `keep` jobs done más recientes; borra el resto. keep<=0 = no-op."""
        if keep <= 0:
            return 0
        with self._lock:
            sub = ("SELECT id FROM jobs WHERE status='done' "
                   "ORDER BY completed DESC LIMIT ?")
            self._conn.execute(
                # subquery construida solo con literales internos
                f"DELETE FROM downloaded_urls WHERE job_id IN "  # nosec B608
                f"(SELECT id FROM jobs WHERE status='done' AND id NOT IN ({sub}))",
                (keep,),
            )
            cur = self._conn.execute(
                # sub construida solo con literales internos
                f"DELETE FROM jobs WHERE status='done' AND id NOT IN ({sub})",  # nosec B608
                (keep,),
            )
            self._conn.commit()
            return cur.rowcount

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM downloaded_urls WHERE job_id=?", (job_id,))
            cur = self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear_history(self) -> int:
        with self._lock:
            self._conn.execute(
                "DELETE FROM downloaded_urls WHERE job_id IN "
                "(SELECT id FROM jobs WHERE status IN ('done', 'error', 'interrupted'))"
            )
            cur = self._conn.execute(
                "DELETE FROM jobs WHERE status IN ('done', 'error', 'interrupted')"
            )
            self._conn.commit()
            return cur.rowcount

    def get_deletable_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, filepath, workdir FROM jobs "
                "WHERE status IN ('done', 'error', 'interrupted')"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_queued(self, limit: int) -> list[dict[str, Any]]:
        # Excluye incógnito: un job incógnito NUNCA se auto-reanuda. Si sobrevive
        # como 'queued' a un restart, reconcile_startup lo borra; este filtro es
        # defensa en profundidad para que dispatch_loop jamás lo despache como
        # descarga normal (perdería el flag y el incognito_dir → fuga a historial).
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status='queued' AND incognito=0 "
                "ORDER BY created LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Dedup / watch mode (poblado después; la API existe ya)
    # ------------------------------------------------------------------ #
    def record_download(
        self, extractor: str, video_id: str, job_id: str,
        channel_id: int | None = None, when: int | None = None,
    ) -> None:
        when = int(time.time()) if when is None else when
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO downloaded_urls "
                "(extractor, video_id, channel_id, job_id, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (extractor, video_id, channel_id, job_id, when),
            )
            self._conn.commit()

    def is_downloaded(self, extractor: str, video_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM downloaded_urls WHERE extractor=? AND video_id=?",
                (extractor, video_id),
            ).fetchone()
        return row is not None

    def has_active_job_for_video(self, extractor: str, video_id: str) -> bool:
        """Indica si hay un job no-terminal para este video (evita duplicados entre
        ciclos del watch loop).

        Limitacion: no detecta jobs manuales en curso porque extractor/video_id
        se asignan recien dentro de _run_download, no al crearse el job."""
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with self._lock:
            row = self._conn.execute(
                # f-string sin interpolacion de input (solo formato multilinea)
                f"SELECT 1 FROM jobs WHERE extractor=? AND video_id=? "  # nosec B608
                f"AND status IN ({placeholders}) LIMIT 1",
                (extractor, video_id, *ACTIVE_STATUSES),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------ #
    # Channels CRUD
    # ------------------------------------------------------------------ #
    _CHANNEL_UPDATABLE = frozenset({"title", "quality", "interval_minutes", "enabled"})

    def insert_channel(
        self, url: str, quality: str = "best",
        interval_minutes: int = 60, created: int | None = None,
    ) -> int:
        created = int(time.time()) if created is None else created
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO channels (url, quality, interval_minutes, created) "
                "VALUES (?, ?, ?, ?)",
                (url, quality, interval_minutes, created),
            )
            self._conn.commit()
            return int(cur.lastrowid) if cur.lastrowid else -1

    def update_channel(self, channel_id: int, **fields: Any) -> None:
        bad = set(fields) - self._CHANNEL_UPDATABLE
        if bad:
            raise ValueError(f"columnas no actualizables: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                # cols de whitelist interna de update_channel, valores parametrizados
                f"UPDATE channels SET {cols} WHERE id=?",  # nosec B608
                (*fields.values(), channel_id),
            )
            self._conn.commit()

    def delete_channel(self, channel_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM channels WHERE id=?", (channel_id,))
            self._conn.commit()

    def get_channel(self, channel_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_channels(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM channels"
        params: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY created"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def touch_channel(self, channel_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE channels SET last_checked=? WHERE id=?",
                (int(time.time()), channel_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # Settings runtime
    # ------------------------------------------------------------------ #
    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated) VALUES (?, ?, ?)",
                (key, value, int(time.time())),
            )
            self._conn.commit()

    def get_all_settings(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def count_jobs_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    def count_jobs_by_workdir(self, workdir: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE workdir=?",
                (workdir,),
            ).fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------ #
    # Settings runtime
    # ------------------------------------------------------------------ #
    def import_history_json(self, entries: list[dict[str, Any]]) -> int:
        """Importa el history.json legacy como jobs 'done'. Solo si `jobs` está vacía.

        Entradas pre-v1.6.0 no tienen `thumbnail` → `.get` con default None (§5.2).
        Idempotente: no hace nada si ya hay jobs."""
        with self._lock:
            count = int(self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            if count > 0:
                return 0
            imported = 0
            for e in entries:
                job_id = str(e.get("job_id") or f"legacy_{imported}")
                self._conn.execute(
                    "INSERT OR IGNORE INTO jobs "
                    "(id, url, quality, status, title, filename, size, thumbnail, "
                    " created, completed) "
                    "VALUES (?, ?, ?, 'done', ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        str(e.get("url", "")),
                        str(e.get("quality", "")),
                        e.get("title"),
                        e.get("filename"),
                        e.get("size"),
                        e.get("thumbnail"),  # None en entradas viejas (§5.2)
                        float(e.get("completed", 0) or 0),
                        e.get("completed"),
                    ),
                )
                imported += 1
            self._conn.commit()
        return imported
