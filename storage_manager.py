"""Gestión de storage: uso, cleanup de workdirs, eviction de jobs en memoria."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from secure_delete import wipe_workdir

log = logging.getLogger("opengrab")


class StorageManager:
    """Maneja el ciclo de vida de workdirs (temp → cleanup) y la evicción
    de jobs viejos de memoria. Depende del DB + dict de jobs de AppState
    (inyectados en el constructor)."""

    def __init__(
        self,
        out_dir: Path,
        db: Any,  # Database (evita import circular)
        jobs: dict[str, Any],  # AppState.jobs
        job_events: dict[str, Any],  # AppState.job_events
        resolve: Any,  # AppState.resolve
    ) -> None:
        self.out_dir = out_dir
        self.db = db
        self.jobs = jobs
        self.job_events = job_events
        self.resolve = resolve

        self._usage_lock = threading.Lock()
        self._usage_cache: int | None = None
        self._usage_cache_ts = 0.0
        self.pending_cleanups: set[str] = set()

    # ------------------------------------------------------------------ #
    # Storage accounting
    # ------------------------------------------------------------------ #
    def invalidate_cache(self) -> None:
        with self._usage_lock:
            self._usage_cache_ts = 0.0

    def _scan_usage_bytes(self) -> int:
        total = 0
        for p in self.out_dir.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    def current_usage_bytes(self, max_age: float = 5.0) -> int:
        now = time.monotonic()
        with self._usage_lock:
            if self._usage_cache is not None and now - self._usage_cache_ts < max_age:
                return self._usage_cache
        total = self._scan_usage_bytes()
        with self._usage_lock:
            self._usage_cache = total
            self._usage_cache_ts = now
        return total

    # ------------------------------------------------------------------ #
    # Filesystem housekeeping
    # ------------------------------------------------------------------ #
    def cleanup_old_workdirs(self) -> None:
        cutoff = time.time() - 86400
        count = 0
        for pattern in ("opengrab_*", "opengrab_batch_*"):
            for d in self.out_dir.glob(pattern):
                if not d.is_dir():
                    continue
                try:
                    is_empty = not any(d.iterdir())
                    if is_empty or d.stat().st_mtime < cutoff:
                        shutil.rmtree(d)
                        count += 1
                except OSError:
                    pass
        if count:
            log.info("limpiados %d workdirs viejos o vacios", count)

    def schedule_tempdir_cleanup(self, workdir: str) -> None:
        self.pending_cleanups.add(workdir)

    def schedule_workdir_if_external(self, job: Any) -> bool:
        if not job.workdir or not job.filepath:
            return False
        wd = Path(job.workdir).resolve()
        if Path(job.filepath).resolve().is_relative_to(wd):
            return False
        self.schedule_tempdir_cleanup(job.workdir)
        job.workdir = ""
        return True

    def flush_pending_cleanups(self) -> int:
        removed = 0
        for dirpath in list(self.pending_cleanups):
            try:
                wipe_workdir(dirpath)
            except OSError:
                log.exception("flush_pending_cleanups: no se pudo borrar %s", dirpath)
            if not os.path.exists(dirpath):
                self.pending_cleanups.discard(dirpath)
                removed += 1
        if removed:
            with self._usage_lock:
                self._usage_cache_ts = 0.0
        return removed

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
                wipe_workdir(str(d))
                freed += freed_before
                cleaned += 1
            except OSError:
                pass
        with self._usage_lock:
            self._usage_cache_ts = 0.0
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
                wipe_workdir(str(d))
                freed += freed_before
                cleaned += 1
            except OSError:
                pass
        with self._usage_lock:
            self._usage_cache_ts = 0.0
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
                other = [
                    j for jid2, j in self.jobs.items()
                    if jid2 != jid and j.workdir == job.workdir
                ]
                if not other:
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
        self.db.prune_history(keep=self.resolve("history_max", 500, int)[0])

        self.flush_pending_cleanups()

        return len(to_delete)

    async def evict_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.evict_once()
