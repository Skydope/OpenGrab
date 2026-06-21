from __future__ import annotations

import asyncio
import json as _json
import logging
import shutil
import threading
import time
from pathlib import Path

from models import Job

log = logging.getLogger("opengrab")


class AppState:
    def __init__(
        self,
        out_dir: Path,
        history_file: Path,
        history_max: int = 500,
    ) -> None:
        self.out_dir = out_dir
        self.history_file = history_file
        self.history_max = history_max
        self.jobs: dict[str, Job] = {}
        self.job_events: dict[str, asyncio.Event] = {}
        self.running_tasks: set = set()
        self.history: list[dict] = []
        self._history_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # History persistence
    # ------------------------------------------------------------------ #
    def load_history(self) -> None:
        try:
            self.history = _json.loads(
                self.history_file.read_text(encoding="utf-8")
            )
        except (OSError, _json.JSONDecodeError):
            self.history = []

    def _write_history(self) -> None:
        entries = self.history[-self.history_max :]
        try:
            self.history_file.write_text(
                _json.dumps(entries, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            pass

    def add_history_entry(self, entry: dict) -> None:
        with self._history_lock:
            self.history.append(entry)
            self._write_history()

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
        return len(to_delete)

    async def evict_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.evict_once()
