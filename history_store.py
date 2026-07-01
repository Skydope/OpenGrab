"""Historial: consulta, borrado individual y limpieza masiva."""

from __future__ import annotations

import logging
from typing import Any

from secure_delete import wipe_file, wipe_workdir

log = logging.getLogger("opengrab")


class HistoryStore:
    """Envuelve operaciones de historial sobre Database."""

    def __init__(self, db: Any, jobs: dict[str, Any], job_events: dict[str, Any]) -> None:
        self.db = db
        self.jobs = jobs
        self.job_events = job_events

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.get_history(limit=limit)
        for r in rows:
            r["job_id"] = r.pop("id", r.get("job_id"))
        return rows  # type: ignore[no-any-return]

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

    def _secure_delete_files(self, filepath: str | None, workdir: str | None) -> None:
        try:
            if filepath:
                wipe_file(str(filepath))
        except OSError:
            pass
        try:
            if workdir:
                if self.db.count_jobs_by_workdir(workdir) == 0:
                    wipe_workdir(str(workdir))
        except OSError:
            pass

    def clear_all_history(self) -> int:
        rows = self.db.get_deletable_jobs()
        for r in rows:
            if r.get("filepath"):
                try:
                    wipe_file(str(r["filepath"]))
                except OSError:
                    pass
        workdirs_seen: set[str] = set()
        for r in rows:
            wd = r.get("workdir")
            if wd and wd not in workdirs_seen:
                workdirs_seen.add(str(wd))
                try:
                    wipe_workdir(str(wd))
                except OSError:
                    pass
        count = self.db.clear_history()
        self.jobs = {k: v for k, v in self.jobs.items()
                     if v.status not in ("done", "error", "interrupted")}
        self.job_events = {k: v for k, v in self.job_events.items()
                           if k in self.jobs}
        return count  # type: ignore[no-any-return]
