from __future__ import annotations

import asyncio
import atexit
import logging
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
