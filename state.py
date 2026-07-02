from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from db import Database
from history_store import HistoryStore
from library_path_resolver import LibraryPathResolver
from models import Job
from storage_manager import StorageManager

import config
from i18n import t

log = logging.getLogger("opengrab")

# Timestamp global del último dispatch de watch mode (para el tray de desktop).
_latest_watch_ts: float = 0.0


class AppState:
    def __init__(
        self,
        db: Database,
        out_dir: Path,
    ) -> None:
        self.db = db
        self.out_dir = out_dir
        self.jobs: dict[str, Job] = {}
        self.job_events: dict[str, asyncio.Event] = {}
        self.cancel_requests: set[str] = set()  # job_ids cuya cancelación se pidió
        self.running_tasks: set[asyncio.Task[None]] = set()
        self._start_time = time.monotonic()
        self._last_watch_dispatch: float = 0.0  # timestamp del último dispatch de watch mode

        # StorageManager: delegates cleanup, usage, and eviction
        self.storage = StorageManager(
            out_dir=out_dir, db=db,
            jobs=self.jobs, job_events=self.job_events, resolve=self.resolve,
        )

        # LibraryPathResolver: templates, dedup, file movement
        self.library = LibraryPathResolver(
            db=db, jobs=self.jobs,
            resolve=self.resolve, resolve_library_dir=self.resolve_library_dir,
        )

        # HistoryStore: historial CRUD
        self.history = HistoryStore(db=db, jobs=self.jobs, job_events=self.job_events)
        atexit.register(self.db.close)

    # ------------------------------------------------------------------ #
    # Settings resolver (env > tabla > ini > default)
    # ------------------------------------------------------------------ #
    def resolve(self, key: str, default: Any, cast: type = str) -> tuple[Any, str]:
        """Resuelve una setting con precedencia env > tabla > ini > default.

        Devuelve (valor, origin) donde origin ∈ {env, table, ini, default}.

        La tabla SQLite (lo que el usuario edita desde la UI) gana sobre el ini.
        El ini es solo una *semilla* que escribe el instalador; una vez que el
        usuario toca una setting, su valor vive en la tabla y se aplica en vivo
        (hot-reload) porque resolve() se consulta en cada uso. ``env`` mantiene
        la máxima precedencia para overrides declarativos de ops (Docker), que
        no se pueden sobrescribir en caliente sin romper esa semántica.
        """
        env_key = config._SETTING_ENV.get(key)
        if env_key and env_key in os.environ:
            return cast(os.environ[env_key]), "env"
        v = self.db.get_setting(key)
        if v is not None:
            return cast(v), "table"
        if key in config._ini:
            return cast(config._ini[key]), "ini"
        return default, "default"

    def resolve_library_dir(self) -> Path:
        """Resuelve library_dir — fuente unica para _finalize_desktop y api_job_file."""
        raw, _ = self.resolve("library_dir", "", str)
        return Path(raw).resolve() if raw else self.out_dir.resolve()

    # ------------------------------------------------------------------ #
    # Job completion
    # ------------------------------------------------------------------ #
    def complete_job(self, job_id: str, **fields: Any) -> None:
        fields.setdefault("status", "done")
        fields.setdefault("completed", int(time.time()))
        try:
            self.db.update_job(job_id, **fields)
        except sqlite3.Error:
            log.exception("job %s: error al persistir en DB", job_id)

    # ------------------------------------------------------------------ #
    # Job helpers
    # ------------------------------------------------------------------ #
    def count_active_jobs(self) -> int:
        return sum(
            1
            for j in self.jobs.values()
            if j.status in ("queued", "starting", "downloading", "processing")
        )

    def dismiss_job_from_view(self, job_id: str) -> bool:
        """Remueve un job terminado de la vista de sesion sin tocar DB ni archivos.

        El job sigue disponible en Historial y sus archivos permanecen intactos.
        Evita que GET /api/jobs lo devuelva al rehidratar la UI tras reload.

        Solo descarta jobs en estado terminal (done/error/cancelled). Un job
        activo no se descarta: la tarea de descarga corre en un thread que
        mantiene su referencia al Job y seguiria viva pero huerfana (invisible,
        ocupando un slot de concurrencia). Devuelve False en ese caso, en
        simetria con cancel_job, que tambien valida el estado.
        """
        job = self.jobs.get(job_id)
        if job is None or job.status not in ("done", "error", "cancelled"):
            return False
        self.jobs.pop(job_id)
        self.job_events.pop(job_id, None)
        return True

    # ------------------------------------------------------------------ #
    # Task lifecycle
    # ------------------------------------------------------------------ #
    def _track_task(self, task: asyncio.Task[None]) -> None:
        self.running_tasks.add(task)
        task.add_done_callback(self.running_tasks.discard)

    def cancel_job(self, job_id: str) -> str:
        """Cancela un job. Devuelve el resultado:

        - "cancelling": estaba corriendo en un worker thread; se marca la
          bandera y el progress hook de yt-dlp aborta en el próximo callback.
        - "cancelled": estaba sólo encolado en DB (sin thread); se pasa a
          'cancelled' para que dispatch_loop no lo tome.
        - "noop": no existe o ya está en un estado terminal.
        """
        job = self.jobs.get(job_id)
        if job is not None and job.status in (
            "queued", "starting", "downloading", "processing"
        ):
            self.cancel_requests.add(job_id)
            return "cancelling"
        row = self.db.get_job(job_id)
        if row is not None and row["status"] == "queued":
            self.db.update_job(job_id, status="cancelled")
            return "cancelled"
        return "noop"

    def _spawn_download(self, job_id: str, url: str, quality: str,
                        subs: bool = False, thumb: bool = False,
                        infojson: bool = False, incognito: bool = False,
                        incognito_dir: str | None = None,
                        playlist_subdir: str | None = None) -> None:
        """Crea Job en memoria + Event y lanza _run_download en thread.

        Precondicion: la fila en DB ya existe en el estado correcto.
        """
        from download import _run_download, DownloadContext  # local: evita el ciclo state<->download

        self.jobs[job_id] = Job(id=job_id, created=time.time(), incognito=incognito)
        self.job_events[job_id] = asyncio.Event()
        loop = asyncio.get_running_loop()
        ctx = DownloadContext(
            job_id=job_id,
            url=url,
            quality=quality,
            subs=subs,
            thumb=thumb,
            infojson=infojson,
            incognito=incognito,
            incognito_dir=incognito_dir,
            playlist_subdir=playlist_subdir,
        )
        task = asyncio.create_task(
            asyncio.to_thread(_run_download, self, ctx, loop)
        )
        self._track_task(task)

    # ------------------------------------------------------------------ #
    # Watch mode scheduler
    # ------------------------------------------------------------------ #
    async def watch_loop(self) -> None:
        global _latest_watch_ts
        from download import _check_channel_watch

        while True:
            await asyncio.sleep(60)
            channels = self.db.list_channels(enabled_only=True)
            now = int(time.time())
            for ch in channels:
                last = ch.get("last_checked") or 0
                interval_s = ch["interval_minutes"] * 60
                if now - last >= interval_s:
                    try:
                        videos = await asyncio.to_thread(
                            _check_channel_watch, self, ch,
                        )
                        self.db.touch_channel(ch["id"])
                        quality = ch["quality"]
                        dispatched = 0
                        for v in videos:
                            # Nota: descargas manuales tienen extractor=NULL hasta que
                            # _run_download completa extract_info. Si watch chequea justo en
                            # ese instante, podria crear un duplicado. Es una ventana de ~1-2s
                            # en un escenario casi imposible (mismo video en canal vigilado +
                            # bajado a mano simultaneo). El fix requeriria _fetch_info previo en
                            # api_create_job -> latencia extra en cada descarga manual.
                            # Decision deliberada: no over-engineerear.
                            if self.db.is_downloaded(v["extractor"], v["video_id"]):
                                continue
                            if self.db.has_active_job_for_video(v["extractor"], v["video_id"]):
                                continue
                            job_id = uuid.uuid4().hex[:12]
                            self.db.insert_job(job_id, v["url"], quality)
                            self.db.update_job(job_id, extractor=v["extractor"], video_id=v["video_id"])
                            log.info(
                                "watch: nuevo video → job %s (%s)", job_id, v.get("title", "?"),
                            )
                            self._spawn_download(job_id, v["url"], quality)
                            dispatched += 1
                        if dispatched:
                            self._last_watch_dispatch = time.time()
                            _latest_watch_ts = time.time()  # global para desktop tray
                            log.info(
                                "watch: canal %s → %d videos despachados",
                                ch.get("title") or ch["url"], dispatched,
                            )
                    except Exception:  # watch_loop: yt-dlp + DB + dispatch; el loop no debe caerse
                        log.exception("watch: error en canal %s", ch["url"])

    # ------------------------------------------------------------------ #
    # Batch dispatch loop (playlist download)
    # ------------------------------------------------------------------ #
    async def dispatch_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            # Limpieza inmediata: cuando termina la última descarga, drenar los
            # workdirs husk ya registrados. Corre en el event loop (no en el
            # worker thread), sin race sobre self.jobs y dándole a Windows un
            # margen para soltar handles antes del rmtree.
            if self.storage._pending_cleanups and self.count_active_jobs() == 0:
                self.storage.flush_pending_cleanups()
            max_jobs = self.resolve("max_jobs", 2, int)[0]
            # MAX_JOBS es un techo de CONCURRENCIA, no de despachos-por-tick. Si ya
            # hay descargas activas (manuales o de un batch anterior), descontamos
            # esos slots; si no, mezclar manual + batch excederia el limite.
            slots = max_jobs - self.count_active_jobs()
            if slots <= 0:
                continue
            queued = self.db.get_queued(limit=slots)
            for job_dict in queued:
                job_id = job_dict["id"]
                if job_id in self.jobs:
                    continue
                max_total_mb = self.resolve("max_total_mb", 0, int)[0]
                if max_total_mb and self.storage.current_usage_bytes() >= max_total_mb * 1024 * 1024:
                    self.db.update_job(job_id, status="error", error=t("error.storage_full_short"))
                    continue
                self.db.update_job(job_id, status="starting")
                self._spawn_download(
                    job_id, job_dict["url"], job_dict["quality"],
                    subs=bool(job_dict.get("subs")),
                    thumb=bool(job_dict.get("thumb")),
                    infojson=bool(job_dict.get("infojson")),
                    playlist_subdir=job_dict.get("playlist_subdir"),
                )
