from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any


from db import Database
from models import Job

log = logging.getLogger("opengrab")


class AppState:
    def __init__(
        self,
        db: Database,
        out_dir: Path,
        history_max: int = 500,
    ) -> None:
        self.db = db
        self.out_dir = out_dir
        self.history_max = history_max
        self.jobs: dict[str, Job] = {}
        self.job_events: dict[str, asyncio.Event] = {}
        self.running_tasks: set[asyncio.Task[None]] = set()
        atexit.register(self.db.close)

    # ------------------------------------------------------------------ #
    # Job completion
    # ------------------------------------------------------------------ #
    def complete_job(self, job_id: str, **fields: Any) -> None:
        fields.setdefault("status", "done")
        fields.setdefault("completed", int(time.time()))
        try:
            self.db.update_job(job_id, **fields)
        except Exception:
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

    # ------------------------------------------------------------------ #
    # Storage accounting
    # ------------------------------------------------------------------ #
    def current_usage_bytes(self) -> int:
        total = 0
        for p in self.out_dir.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    # ------------------------------------------------------------------ #
    # Filesystem housekeeping
    # ------------------------------------------------------------------ #
    def cleanup_old_workdirs(self) -> None:
        cutoff = time.time() - 86400
        count = 0
        for d in self.out_dir.glob("opengrab_*"):
            if d.is_dir():
                try:
                    if d.stat().st_mtime < cutoff:
                        shutil.rmtree(d)
                        count += 1
                except OSError:
                    pass
        if count:
            log.info("limpiados %d workdirs viejos", count)

    # ------------------------------------------------------------------ #
    # Secure file deletion (3-pass: 0x00, 0xFF, random — no external tool)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _secure_delete_file(filepath: str) -> None:
        """Sobrescribe el archivo (0x00 / 0xFF / random) y lo borra.

        CAVEAT: el overwrite in-place solo da garantias reales sobre medios que
        reescriben el mismo sector (HDD magnetico). En SSD/NVMe (wear-leveling),
        filesystems copy-on-write (Btrfs, ZFS, APFS) o con snapshots, los datos
        viejos pueden persistir en bloques no mapeados que estas pasadas no tocan.
        En esos medios esto reduce la recuperacion casual pero NO es un borrado
        forense garantizado; para eso hace falta cifrado en reposo o TRIM/secure-erase
        a nivel de dispositivo. Mantenemos las 3 pasadas porque no hacen daño y
        ayudan en el caso HDD, sin venderlas como mas de lo que son.
        """
        path = Path(filepath)
        if not path.is_file():
            return
        size = path.stat().st_size
        if size == 0:
            path.unlink()
            return
        try:
            with open(path, "r+b") as f:
                # Pass 1: zeros
                f.seek(0)
                remaining = size
                while remaining > 0:
                    chunk = min(remaining, 1024 * 1024)
                    f.write(b"\x00" * chunk)
                    remaining -= chunk
                f.flush()
                os.fsync(f.fileno())
                # Pass 2: ones (0xFF)
                f.seek(0)
                remaining = size
                while remaining > 0:
                    chunk = min(remaining, 1024 * 1024)
                    f.write(b"\xFF" * chunk)
                    remaining -= chunk
                f.flush()
                os.fsync(f.fileno())
                # Pass 3: random
                f.seek(0)
                remaining = size
                while remaining > 0:
                    chunk = min(remaining, 1024 * 1024)
                    f.write(os.urandom(chunk))
                    remaining -= chunk
                f.flush()
                os.fsync(f.fileno())
            path.unlink()
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass

    @classmethod
    def _secure_delete_workdir(cls, workdir: str) -> None:
        wd = Path(workdir)
        if not wd.is_dir():
            return
        for f in wd.rglob("*"):
            if f.is_file():
                cls._secure_delete_file(str(f))
        shutil.rmtree(wd, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # History management
    # ------------------------------------------------------------------ #
    def _secure_delete_files(self, filepath: str | None, workdir: str | None) -> None:
        """Borra archivos en background. Nunca raisea — el DB delete ya ocurrio."""
        try:
            if filepath:
                self._secure_delete_file(str(filepath))
        except Exception:
            pass
        try:
            if workdir:
                self._secure_delete_workdir(str(workdir))
        except Exception:
            pass

    def delete_history_entry(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        if job is None:
            return False
        ok = self.db.delete_job(job_id)
        self.jobs.pop(job_id, None)
        self.job_events.pop(job_id, None)
        if not ok:
            log.warning("delete_history_entry: delete_job no afecto filas para %s", job_id)
        else:
            log.info("delete_history_entry: borrado job %s de la DB", job_id)
        filepath = job.get("filepath")
        workdir = job.get("workdir")
        if filepath:
            self._secure_delete_file(str(filepath))
        if workdir:
            self._secure_delete_workdir(str(workdir))
        return ok

    def clear_all_history(self) -> int:
        rows = self.db.get_deletable_jobs()
        for r in rows:
            if r.get("filepath"):
                try:
                    self._secure_delete_file(str(r["filepath"]))
                except Exception:
                    pass
        workdirs_seen: set[str] = set()
        for r in rows:
            wd = r.get("workdir")
            if wd and wd not in workdirs_seen:
                workdirs_seen.add(str(wd))
                try:
                    self._secure_delete_workdir(str(wd))
                except Exception:
                    pass
        count = self.db.clear_history()
        self.jobs = {k: v for k, v in self.jobs.items()
                     if v.status not in ("done", "error", "interrupted")}
        self.job_events = {k: v for k, v in self.job_events.items()
                           if k in self.jobs}
        return count

    # ------------------------------------------------------------------ #
    # Storage info
    # ------------------------------------------------------------------ #
    def list_storage(self) -> dict[str, Any]:
        active_workdirs: set[str] = set()
        for j in self.jobs.values():
            if j.status in ("queued", "starting", "downloading", "processing") and j.workdir:
                active_workdirs.add(j.workdir)
        workdirs: list[dict[str, Any]] = []
        for d in self.out_dir.glob("opengrab_*"):
            if not d.is_dir():
                continue
            size = sum(
                f.stat().st_size for f in d.rglob("*") if f.is_file()
            )
            age_h = (time.time() - d.stat().st_mtime) / 3600
            workdirs.append({
                "name": d.name,
                "size_bytes": size,
                "age_hours": round(age_h, 1),
                "active": str(d) in active_workdirs,
            })
        workdirs.sort(key=lambda w: w["age_hours"])
        loose: list[dict[str, Any]] = []
        for f in self.out_dir.iterdir():
            if f.is_file() and f.name != "opengrab.db":
                loose.append({
                    "name": f.name,
                    "size_bytes": f.stat().st_size,
                    "age_hours": round((time.time() - f.stat().st_mtime) / 3600, 1),
                })
        return {
            "total_usage_bytes": self.current_usage_bytes(),
            "workdirs": workdirs,
            "loose_files": loose,
            "db_size_bytes": Path(self.db.path).stat().st_size if Path(self.db.path).exists() else 0,
        }

    def cleanup_storage(self, max_age_hours: float = 24, dry_run: bool = False) -> dict[str, Any]:
        cutoff = time.time() - max_age_hours * 3600
        cleaned = 0
        freed = 0
        to_clean: list[Path] = []
        for d in self.out_dir.glob("opengrab_*"):
            if d.is_dir() and d.stat().st_mtime < cutoff:
                to_clean.append(d)
        if dry_run:
            for d in to_clean:
                freed += sum(
                    f.stat().st_size for f in d.rglob("*") if f.is_file()
                )
            return {"cleaned": 0, "freed_bytes": freed, "dry_run": True,
                    "would_clean": len(to_clean)}
        for d in to_clean:
            try:
                freed_before = sum(
                    f.stat().st_size for f in d.rglob("*") if f.is_file()
                )
                self._secure_delete_workdir(str(d))
                freed += freed_before
                cleaned += 1
            except Exception:
                pass
        return {"cleaned": cleaned, "freed_bytes": freed}

    def cleanup_storage_all(self) -> dict[str, Any]:
        cleaned = 0
        freed = 0
        for d in self.out_dir.glob("opengrab_*"):
            if not d.is_dir():
                continue
            try:
                freed_before = sum(
                    f.stat().st_size for f in d.rglob("*") if f.is_file()
                )
                self._secure_delete_workdir(str(d))
                freed += freed_before
                cleaned += 1
            except Exception:
                pass
        return {"cleaned": cleaned, "freed_bytes": freed}

    # ------------------------------------------------------------------ #
    # Background eviction
    # ------------------------------------------------------------------ #
    def evict_once(self, cutoff_age: float = 3600) -> int:
        cutoff = time.time() - cutoff_age
        to_delete = [
            jid
            for jid, j in self.jobs.items()
            if j.status in ("done", "error") and j.created < cutoff
        ]
        for jid in to_delete:
            job = self.jobs[jid]
            if job.workdir:
                wd = Path(job.workdir)
                if wd.exists():
                    try:
                        shutil.rmtree(wd, ignore_errors=True)
                    except OSError:
                        pass
            del self.jobs[jid]
            self.job_events.pop(jid, None)
        if to_delete:
            log.info("evacuados %d jobs viejos de memoria", len(to_delete))
        self.db.prune_history(keep=self.history_max)
        return len(to_delete)

    async def evict_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.evict_once()

    # ------------------------------------------------------------------ #
    # Watch mode scheduler
    # ------------------------------------------------------------------ #
    async def watch_loop(self) -> None:
        from download import _check_channel_watch, _run_download

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
                            self.jobs[job_id] = Job(id=job_id, created=time.time())
                            self.job_events[job_id] = asyncio.Event()
                            self.db.insert_job(job_id, v["url"], quality)
                            self.db.update_job(job_id, extractor=v["extractor"], video_id=v["video_id"])
                            log.info(
                                "watch: nuevo video → job %s (%s)", job_id, v.get("title", "?"),
                            )
                            loop = asyncio.get_running_loop()
                            task = asyncio.create_task(
                                asyncio.to_thread(_run_download, self, job_id, v["url"], quality, loop)
                            )
                            self.running_tasks.add(task)
                            task.add_done_callback(self.running_tasks.discard)
                            dispatched += 1
                        if dispatched:
                            log.info(
                                "watch: canal %s → %d videos despachados",
                                ch.get("title") or ch["url"], dispatched,
                            )
                    except Exception:
                        log.exception("watch: error en canal %s", ch["url"])

    # ------------------------------------------------------------------ #
    # Batch dispatch loop (playlist download)
    # ------------------------------------------------------------------ #
    async def dispatch_loop(self) -> None:
        import sys as _sys

        from download import _run_download

        while True:
            await asyncio.sleep(2.0)
            cfg = _sys.modules["config"]
            # MAX_JOBS es un techo de CONCURRENCIA, no de despachos-por-tick. Si ya
            # hay descargas activas (manuales o de un batch anterior), descontamos
            # esos slots; si no, mezclar manual + batch excederia el limite.
            slots = cfg.MAX_JOBS - self.count_active_jobs()
            if slots <= 0:
                continue
            queued = self.db.get_queued(limit=slots)
            for job_dict in queued:
                job_id = job_dict["id"]
                if job_id in self.jobs:
                    continue
                if cfg.MAX_TOTAL_MB and self.current_usage_bytes() >= cfg.MAX_TOTAL_MB * 1024 * 1024:
                    self.db.update_job(job_id, status="error", error="Almacenamiento lleno")
                    continue
                self.db.update_job(job_id, status="starting")
                self.jobs[job_id] = Job(id=job_id, created=time.time())
                self.job_events[job_id] = asyncio.Event()
                loop = asyncio.get_running_loop()
                task = asyncio.create_task(
                    asyncio.to_thread(_run_download, self, job_id, job_dict["url"], job_dict["quality"], loop)
                )
                self.running_tasks.add(task)
                task.add_done_callback(self.running_tasks.discard)
