from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import yt_dlp

from config import FORMATS, MAX_SIZE_MB
from models import Job
from state import AppState

log = logging.getLogger("opengrab")

# --------------------------------------------------------------------------- #
# URL validation helpers
# --------------------------------------------------------------------------- #
_SUPPORTED_RE = re.compile(
    r"^https?://("
    r"(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/|live/|playlist\?|embed/)|youtu\.be/)|"
    r"(www\.)?vimeo\.com/\d+|"
    r"(www\.)?(x\.com|twitter\.com)/\w+/status/\d+|"
    r"(www\.)?tiktok\.com/@?[\w.]+/video/\d+|"
    r"(www\.)?instagram\.com/(p|reel|tv)/[\w-]+"
    r")",
    re.IGNORECASE,
)


def _looks_like_supported(url: str) -> bool:
    return bool(_SUPPORTED_RE.match(url.strip()))


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\s.\-()\[\]]", "", name, flags=re.UNICODE).strip()
    return (name or "video")[:120]


def _sanitize_url(url: str) -> str:
    clean = re.sub(r"([?&]token=)[^&]+", r"\1***", url)
    clean = clean.replace("\n", "").replace("\r", "")
    return clean[:200]


# --------------------------------------------------------------------------- #
# yt-dlp wrappers
# --------------------------------------------------------------------------- #
def _fetch_info(url: str) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("yt-dlp no devolvió información.")
    return info


def _fetch_playlist(url: str) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("yt-dlp no devolvió información.")
    entries = info.get("entries") or []
    videos = []
    for e in entries:
        if e is None:
            continue
        vid_url = e.get("url") or e.get("webpage_url") or ""
        if vid_url:
            videos.append({
                "title": e.get("title", "—"),
                "url": vid_url,
                "duration": e.get("duration"),
            })
    return {
        "title": info.get("title", "—"),
        "count": len(videos),
        "videos": videos,
    }


# --------------------------------------------------------------------------- #
# Download job (runs in thread pool)
# --------------------------------------------------------------------------- #
def _run_download(state: AppState, job_id: str, url: str, quality: str, loop) -> None:
    job = state.jobs[job_id]
    workdir = Path(tempfile.mkdtemp(prefix="opengrab_", dir=state.out_dir))
    job.workdir = str(workdir)

    def hook(d: Dict[str, Any]) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else 0.0
            job.status = "downloading"
            job.percent = round(pct, 1)
            job.speed = d.get("_speed_str", "").strip()
            job.eta = d.get("_eta_str", "").strip()
            job.downloaded = done
            job.total = total
            loop.call_soon_threadsafe(job.event.set)
        elif status == "finished":
            job.status = "processing"
            job.percent = 100.0
            job.note = (
                "Extrayendo audio y convirtiendo a mp3…"
                if quality == "audio"
                else "Muxeando / remuxeando a mp4…"
            )
            loop.call_soon_threadsafe(job.event.set)

    is_audio = quality == "audio"
    outtmpl = str(workdir / "%(title)s.%(ext)s")
    fmt = FORMATS.get(quality, FORMATS["best"])
    if MAX_SIZE_MB and not is_audio:
        fmt += f"[filesize_approx<{MAX_SIZE_MB}M]"
    opts: Dict[str, Any] = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "postprocessors": [],
    }
    if is_audio:
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
        opts.pop("merge_output_format", None)

    try:
        job.status = "starting"
        job.percent = 0.0
        log.info(
            "job %s: iniciando descarga (%s, %s)",
            job_id, quality, _sanitize_url(url),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            raise RuntimeError("yt-dlp no devolvió información.")

        produced = sorted(
            workdir.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        produced = [p for p in produced if p.is_file()]
        if not produced:
            raise RuntimeError("No se generó ningún archivo.")
        final = produced[0]

        title = _safe_name(info.get("title", "video"))
        ext = final.suffix.lstrip(".")
        job.status = "done"
        job.percent = 100.0
        job.filepath = str(final)
        job.filename = f"{title}.{ext}"
        job.mime = "audio/mpeg" if is_audio else "video/mp4"
        job.title = title
        log.info("job %s: completado → %s", job_id, f"{title}.{ext}")
        state.add_history_entry({
            "url": url,
            "title": title,
            "quality": quality,
            "filename": f"{title}.{ext}",
            "size": final.stat().st_size,
            "job_id": job_id,
            "completed": int(time.time()),
        })
        loop.call_soon_threadsafe(job.event.set)
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        loop.call_soon_threadsafe(job.event.set)
        log.error("job %s: falló", job_id, exc_info=True)
    finally:
        loop.call_soon_threadsafe(job.event.set)
