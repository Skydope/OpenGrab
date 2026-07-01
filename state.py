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

from secure_delete import wipe_file, wipe_workdir


from db import Database
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
    # History
    # ------------------------------------------------------------------ #
    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.get_history(limit=limit)
        for r in rows:
            r["job_id"] = r.pop("id", r.get("job_id"))
        return rows

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
    # Storage (delega en StorageManager) — wrappers temporales (→ commit 5)
    # ------------------------------------------------------------------ #
    def _scan_usage_bytes(self) -> int:
        return self.storage._scan_usage_bytes()

    def current_usage_bytes(self, max_age: float = 5.0) -> int:
        return self.storage.current_usage_bytes(max_age)

    def cleanup_old_workdirs(self) -> None:
        return self.storage.cleanup_old_workdirs()

    def _schedule_tempdir_cleanup(self, workdir: str) -> None:
        self.storage._schedule_tempdir_cleanup(workdir)

    def schedule_workdir_if_external(self, job: Job) -> bool:
        return self.storage.schedule_workdir_if_external(job)

    def flush_pending_cleanups(self) -> int:
        return self.storage.flush_pending_cleanups()

    def list_storage(self) -> dict[str, Any]:
        return self.storage.list_storage()

    def cleanup_storage(self, max_age_hours: float = 24, dry_run: bool = False) -> dict[str, Any]:
        return self.storage.cleanup_storage(max_age_hours, dry_run)

    def cleanup_storage_all(self) -> dict[str, Any]:
        return self.storage.cleanup_storage_all()

    def evict_once(self, cutoff_age: float = 3600) -> int:
        return self.storage.evict_once(cutoff_age)

    async def evict_loop(self) -> None:
        await self.storage.evict_loop()

    # ------------------------------------------------------------------ #
    # Secure file deletion (3-pass: 0x00, 0xFF, random — no external tool)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _secure_delete_file(filepath: str, force: bool = False) -> None:
        """Wrapper temporal (→ commit 5): delega en secure_delete.wipe_file."""
        wipe_file(filepath, force)

    @classmethod
    def _secure_delete_workdir(cls, workdir: str, force: bool = False) -> None:
        """Wrapper temporal (→ commit 5): delega en secure_delete.wipe_workdir."""
        wipe_workdir(workdir, force)

    # ------------------------------------------------------------------ #
    # History management
    # ------------------------------------------------------------------ #
    def _secure_delete_files(self, filepath: str | None, workdir: str | None) -> None:
        try:
            if filepath:
                self._secure_delete_file(str(filepath))
        except OSError:
            pass
        try:
            if workdir:
                if self.db.count_jobs_by_workdir(workdir) == 0:
                    self._secure_delete_workdir(str(workdir))
        except OSError:
            pass

    def delete_history_entry(
        self, job_id: str,
    ) -> tuple[str | None, str | None] | None:
        job = self.db.get_job(job_id)
        if job is None:
            return None
        ok = self.db.delete_job(job_id)
        self.jobs.pop(job_id, None)
        self.job_events.pop(job_id, None)
        if not ok:
            log.warning("delete_history_entry: delete_job no afecto filas para %s", job_id)
            return None
        log.info("delete_history_entry: borrado job %s de la DB", job_id)
        return job.get("filepath"), job.get("workdir")

    def clear_all_history(self) -> int:
        rows = self.db.get_deletable_jobs()
        for r in rows:
            if r.get("filepath"):
                try:
                    self._secure_delete_file(str(r["filepath"]))
                except OSError:
                    pass
        workdirs_seen: set[str] = set()
        for r in rows:
            wd = r.get("workdir")
            if wd and wd not in workdirs_seen:
                workdirs_seen.add(str(wd))
                try:
                    self._secure_delete_workdir(str(wd))
                except OSError:
                    pass
        count = self.db.clear_history()
        self.jobs = {k: v for k, v in self.jobs.items()
                     if v.status not in ("done", "error", "interrupted")}
        self.job_events = {k: v for k, v in self.job_events.items()
                           if k in self.jobs}
        self.storage.invalidate_cache()
        return count

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
                self.flush_pending_cleanups()
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
                if max_total_mb and self.current_usage_bytes() >= max_total_mb * 1024 * 1024:
                    self.db.update_job(job_id, status="error", error=t("error.storage_full_short"))
                    continue
                self.db.update_job(job_id, status="starting")
                self._spawn_download(
                    job_id, job_dict["url"], job_dict["quality"],
                    playlist_subdir=job_dict.get("playlist_subdir"),
                )

    # ------------------------------------------------------------------ #
    # Library path resolution (delega en LibraryPathResolver) — wrappers
    # temporales (→ commit 5)
    # ------------------------------------------------------------------ #
    def _resolve_template(self, template: str, info: dict[str, Any], ext: str) -> Path:
        return self.library._resolve_template(template, info, ext)

    def _deduplicate(self, path: Path) -> Path:
        return self.library._deduplicate(path)

    def _finalize_desktop(
        self, job_id: str, workdir: Path, final: Path,
        info: dict[str, Any], quality: str,
        playlist_subdir: str | None = None,
    ) -> None:
        self.library._finalize_desktop(job_id, workdir, final, info, quality, playlist_subdir)

    def _move_file_locked(self, src: Path, dest_dir: Path) -> Path:
        return self.library._move_file_locked(src, dest_dir)

    def move_job_file(self, job_id: str, dest_dir: Path) -> Path:
        return self.library.move_job_file(job_id, dest_dir)

    def _move_incognito(self, src: Path, dest_dir: Path) -> Path:
        return self.library._move_incognito(src, dest_dir)
