"""Path resolution: templates, dedup, file movement (server + incognito + desktop)."""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any

import config
from i18n import t

log = logging.getLogger("opengrab")


class LibraryPathResolver:
    """Resuelve dónde va un archivo y cómo moverlo: name_template,
    deduplicación, movimiento atómico con lock, finalize de escritorio."""

    _ILLEGAL_CHARS = re.compile(r'[\x00-\x1f\x7f\\/:*?"<>|]')

    def __init__(
        self,
        db: Any,          # Database
        jobs: dict[str, Any],  # AppState.jobs
        resolve: Any,     # AppState.resolve
        resolve_library_dir: Any,  # AppState.resolve_library_dir
    ) -> None:
        self.db = db
        self.jobs = jobs
        self.resolve = resolve
        self.resolve_library_dir = resolve_library_dir
        self._finalize_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Name template
    # ------------------------------------------------------------------ #
    def _resolve_template(
        self, template: str, info: dict[str, Any], ext: str
    ) -> Path:
        upload_date = ""
        upload_year = ""
        if "upload_date" in info:
            raw = str(info["upload_date"])[:8]
            if len(raw) == 8 and raw.isdigit():
                upload_date = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
                upload_year = raw[0:4]

        resolution = ""
        formats: list[dict[str, Any]] = info.get("formats") or []
        for f in formats:
            if f.get("vcodec") != "none" and f.get("filesize"):
                resolution = f.get("resolution") or ""
                break

        replacements: dict[str, str] = {
            "{title}":      str(info.get("title") or "").strip(),
            "{channel}":    str(info.get("uploader") or info.get("channel") or "").strip(),
            "{upload_year}": upload_year,
            "{upload_date}": upload_date,
            "{extractor}":  str(info.get("extractor_key") or info.get("extractor") or "").strip(),
            "{video_id}":   str(info.get("id") or "").strip(),
            "{resolution}": resolution,
        }

        def _sanitize_segment(segment: str) -> str:
            seg = self._ILLEGAL_CHARS.sub("", segment)
            seg = re.sub(r"\s+", " ", seg).strip()
            if len(seg) > 120:
                seg = seg[:120].rstrip()
            return seg

        parts = []
        for part in template.replace("\\", "/").split("/"):
            part = part.strip()
            if not part:
                continue
            expanded = part
            for token, value in replacements.items():
                if token in expanded:
                    expanded = expanded.replace(token, value)
            if "{title}" in template and expanded.strip() == "":
                expanded = "video"
            sanitized = _sanitize_segment(expanded)
            if sanitized:
                parts.append(sanitized)

        if not parts:
            parts = ["video"]

        last = parts[-1] if parts else "video"
        parts[-1] = f"{last}.{ext.lstrip('.')}"
        return Path(*parts)

    # ------------------------------------------------------------------ #
    # Deduplication
    # ------------------------------------------------------------------ #
    def deduplicate(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    # ------------------------------------------------------------------ #
    # Core movement (locked)
    # ------------------------------------------------------------------ #
    def _move_file_locked(self, src: Path, dest_dir: Path) -> Path:
        with self._finalize_lock:
            dest_dir = dest_dir.expanduser()
            if dest_dir.exists() and not dest_dir.is_dir():
                raise NotADirectoryError(t("error.dest_not_dir"))
            if src.resolve().parent == dest_dir.resolve():
                return src
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = self.deduplicate(dest_dir / src.name)
            shutil.move(str(src), str(target))
            return target

    # ------------------------------------------------------------------ #
    # Public move API
    # ------------------------------------------------------------------ #
    def move_job_file(self, job_id: str, dest_dir: Path) -> Path:
        job = self.jobs.get(job_id)
        if job is None or not job.filepath:
            raise FileNotFoundError(t("error.job_no_file"))
        if job.status != "done":
            raise ValueError(t("error.job_not_done"))
        src = Path(job.filepath)
        if not src.exists():
            raise FileNotFoundError(t("error.file_not_on_disk"))

        target = self._move_file_locked(src, dest_dir)
        if target == src:
            return target
        log.info("move_job_file: %s -> %s", src.name, target)
        job.filepath = str(target)
        try:
            import sqlite3
            self.db.update_job(job_id, filepath=str(target))
        except sqlite3.Error:
            log.warning("move_job_file: no se pudo persistir filepath en DB",
                        exc_info=True)
        return target

    def move_incognito(self, src: Path, dest_dir: Path) -> Path:
        target = self._move_file_locked(src, dest_dir)
        log.info("incognito move: archivo entregado a carpeta destino")
        return target

    # ------------------------------------------------------------------ #
    # Desktop finalize
    # ------------------------------------------------------------------ #
    def finalize_desktop(
        self,
        job_id: str,
        workdir: Path,
        final: Path,
        info: dict[str, Any],
        quality: str,
        playlist_subdir: str | None = None,
    ) -> None:
        if not config.IS_DESKTOP:
            return
        with self._finalize_lock:
            library_dir = self.resolve_library_dir()
            if playlist_subdir:
                library_dir = library_dir / playlist_subdir
            template, _ = self.resolve("name_template", "{title}", str)

            ext = final.suffix.lstrip(".") or ("mp3" if quality == "audio" else "mp4")
            relative = self._resolve_template(template, info, ext)
            target = library_dir / relative
            target = self.deduplicate(target)

            if target.exists() and target.samefile(final):
                return

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(final), str(target))
                log.info("desktop_finalize: %s -> %s", final.name, target)
                job = self.jobs.get(job_id)
                if job:
                    job.filepath = str(target)
            except OSError as exc:
                log.warning("desktop_finalize: falló movimiento %s -> %s: %s",
                            final, target, exc)
        return
