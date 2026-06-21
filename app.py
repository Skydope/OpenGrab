#!/usr/bin/env python3
"""
ytgrab — self-hosted YouTube downloader (yt-dlp wrapper).

Paste a URL, get an MP4 (or MP3). Backend is FastAPI + yt-dlp + ffmpeg.
Frontend is vanilla HTML/CSS/JS served from static/. Designed for homelab/LAN.

Usage:
    pip install -r requirements.txt
    python app.py
    # -> http://127.0.0.1:8800

Environment:
    YTGRAB_HOST   (default 127.0.0.1)
    YTGRAB_PORT   (default 8800)
    YTGRAB_DIR    (default ./downloads)
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
import yt_dlp


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HOST = os.environ.get("YTGRAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("YTGRAB_PORT", "8800"))
OUT_DIR = Path(os.environ.get("YTGRAB_DIR", "./downloads")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_old_workdirs() -> None:
    """Borra workdirs temporales de yt-dlp que hayan quedado de ejecuciones
    anteriores (más de 24 h)."""
    cutoff = time.time() - 86400
    count = 0
    for d in OUT_DIR.glob("ytgrab_*"):
        if d.is_dir():
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d)
                    count += 1
            except OSError:
                pass
    if count:
        log.info("limpiados %d workdirs viejos", count)


_cleanup_old_workdirs()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ytgrab] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ytgrab")

TOKEN = os.environ.get("YTGRAB_TOKEN", "").strip()
MAX_JOBS = int(os.environ.get("YTGRAB_MAX_JOBS", "2"))
MAX_SIZE_MB = max(0, int(os.environ.get("YTGRAB_MAX_SIZE_MB", "0")))
HISTORY_FILE = OUT_DIR / ".ytgrab_history.json"
HISTORY_MAX = 500


def _load_history() -> list:
    try:
        return _json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return []


def _save_history(entries: list) -> None:
    entries = entries[-HISTORY_MAX:]
    try:
        HISTORY_FILE.write_text(_json.dumps(entries, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass


HISTORY: list = _load_history()


def require_auth(request: Request) -> None:
    """Dependencia de FastAPI que exige token si YTGRAB_TOKEN esta seteado.
    Acepta: Authorization Bearer, ?token= query param, o cookie ytgrab_token."""
    if not TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:] == TOKEN:
        return
    if request.query_params.get("token") == TOKEN:
        return
    if request.cookies.get("ytgrab_token") == TOKEN:
        return
    raise HTTPException(401, "Token requerido. Usa Authorization: Bearer <token> o ?token=...")


_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

app = FastAPI(title="ytgrab")

# Rate limiting
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
limiter = Limiter(key_func=get_remote_address, default_limits=["30/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda req, exc: JSONResponse(
    status_code=429, content={"detail": "Demasiadas solicitudes. Espera un momento."},
))

# Montar archivos estáticos (CSS, JS)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Estado de jobs en memoria. Tool de un solo usuario / homelab: alcanza y sobra.
JOBS: Dict[str, Job] = {}

# Selectores de formato para yt-dlp. Todos terminan remuxeando a mp4
# (merge_output_format). Se prefiere h264/m4a para maxima compatibilidad.
FORMATS: Dict[str, str] = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bv*+ba/b",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=480][ext=mp4]/bv*[height<=480]+ba/b[height<=480]",
    "audio": "bestaudio/best",  # se posprocesa a mp3
}


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
class JobReq(BaseModel):
    url: str
    quality: str = "best"


class AuthReq(BaseModel):
    token: str


class Job(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    id: str
    status: str = "queued"
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    note: str = ""
    filename: str = ""
    error: str = ""
    filepath: str = ""
    mime: str = ""
    created: float = 0.0
    workdir: str = ""
    downloaded: int = 0
    total: int = 0
    title: str = ""
    event: asyncio.Event = Field(default_factory=asyncio.Event, exclude=True)


# --------------------------------------------------------------------------- #
# Helpers
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
    return {"title": info.get("title", "—"), "count": len(videos), "videos": videos}


def _run_download(job_id: str, url: str, quality: str) -> None:
    job = JOBS[job_id]
    workdir = Path(tempfile.mkdtemp(prefix="ytgrab_", dir=OUT_DIR))
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
            job.event.set()
        elif status == "finished":
            job.status = "processing"
            job.percent = 100.0
            job.note = "Muxeando / remuxeando a mp4…"
            job.event.set()

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
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": "192"}
        ]
        opts.pop("merge_output_format", None)

    try:
        job.status = "starting"
        job.percent = 0.0
        log.info("job %s: iniciando descarga (%s, %s)", job_id, quality, url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

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
        HISTORY.append({
            "url": url, "title": title, "quality": quality,
            "filename": f"{title}.{ext}", "size": final.stat().st_size,
            "job_id": job_id, "completed": int(time.time()),
        })
        _save_history(HISTORY)
        job.event.set()
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = str(exc)
        job.event.set()
        log.error("job %s: falló — %s", job_id, exc)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.post("/api/auth")
async def api_auth(req: AuthReq, response: Response):
    if not TOKEN:
        return {"ok": True}
    if req.token != TOKEN:
        raise HTTPException(401, "Token invalido.")
    response.set_cookie(
        key="ytgrab_token", value=TOKEN,
        httponly=True, samesite="lax", max_age=86400 * 30, path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie(key="ytgrab_token", path="/")
    return {"ok": True}


@app.get("/api/info")
@limiter.limit("10/minute")
async def api_info(request: Request, url: str, _: None = Depends(require_auth)):
    url = url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(400, "URL no soportada. Probá con YouTube, Vimeo, TikTok, X o Instagram.")
    try:
        info = await asyncio.to_thread(_fetch_info, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo leer el video: {exc}")

    dur = info.get("duration") or 0
    raw_formats = info.get("formats") or []
    formats: list = []
    for f in raw_formats:
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        fmt = {
            "format_id": f.get("format_id", ""),
            "ext": f.get("ext", ""),
            "resolution": f.get("resolution") or "",
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "vcodec": f.get("vcodec") or "",
            "acodec": f.get("acodec") or "",
            "tbr": f.get("tbr"),
            "format_note": f.get("format_note") or "",
        }
        formats.append(fmt)
    # Limit to most relevant (prefer ones with filesize, cap at 20)
    formats.sort(key=lambda x: (0 if x["filesize"] else 1, -(x["tbr"] or 0)))
    formats = formats[:20]
    return JSONResponse({
        "title": info.get("title", "—"),
        "channel": info.get("uploader") or info.get("channel") or "—",
        "duration": dur,
        "duration_str": time.strftime("%H:%M:%S", time.gmtime(dur)) if dur else "—",
        "thumbnail": info.get("thumbnail"),
        "view_count": info.get("view_count"),
        "formats": formats,
    })


@app.get("/api/playlist")
async def api_playlist(url: str, _: None = Depends(require_auth)):
    url = url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(400, "URL no soportada. Probá con YouTube, Vimeo, TikTok, X o Instagram.")
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer la playlist: {exc}")
    return JSONResponse(info)


@app.post("/api/jobs")
@limiter.limit("5/minute")
async def api_create_job(request: Request, req: JobReq, _: None = Depends(require_auth)):
    url = req.url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(400, "URL no soportada. Probá con YouTube, Vimeo, TikTok, X o Instagram.")
    if req.quality not in FORMATS:
        raise HTTPException(400, "Calidad inválida.")
    active = sum(1 for j in JOBS.values() if j.status in
                 ("queued", "starting", "downloading", "processing"))
    if active >= MAX_JOBS:
        raise HTTPException(429, f"Límite de {MAX_JOBS} descarga(s) simultánea(s). Esperá que termine una.")

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = Job(id=job_id, created=time.time())
    log.info("job %s: creado (%s, %s)", job_id, req.quality, req.url)
    # Lanzar en thread aparte (yt-dlp es bloqueante).
    asyncio.create_task(asyncio.to_thread(_run_download, job_id, url, req.quality))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}/events")
async def api_job_events(job_id: str, _: None = Depends(require_auth)):
    if job_id not in JOBS:
        raise HTTPException(404, "Job no encontrado.")

    async def stream():
        last = None
        while True:
            job = JOBS.get(job_id)
            if job is None:
                break
            event = job.event
            if event:
                try:
                    await asyncio.wait_for(event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                event.clear()
            snapshot = {
                "status": job.status,
                "percent": job.percent,
                "speed": job.speed,
                "eta": job.eta,
                "note": job.note,
                "filename": job.filename,
                "error": job.error,
            }
            if snapshot != last:
                yield f"data: {_json.dumps(snapshot)}\n\n"
                last = snapshot
            if job.status in ("done", "error"):
                break

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{job_id}/file")
async def api_job_file(job_id: str, _: None = Depends(require_auth)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado.")
    if job.status != "done":
        raise HTTPException(409, "El archivo todavía no está listo.")
    path = job.filepath
    if not path or not Path(path).exists():
        raise HTTPException(410, "El archivo ya no está disponible.")
    log.info("job %s: sirviendo archivo → %s", job_id, job.filename)
    return FileResponse(path, media_type=job.mime or "application/octet-stream",
                        filename=job.filename or "download")


@app.get("/api/history")
async def api_history(limit: int = 20, _: None = Depends(require_auth)):
    entries = HISTORY[-max(1, min(limit, HISTORY_MAX)):]
    return JSONResponse(list(reversed(entries)))


@app.get("/health")
async def health():
    active = sum(1 for j in JOBS.values() if j.status in
                 ("queued", "starting", "downloading", "processing"))
    return {"status": "ok", "jobs_active": active}


@app.get("/", response_class=HTMLResponse)
async def index():
    html = _INDEX_HTML.replace("__AUTH_REQUIRED__", "true" if TOKEN else "false")
    html = html.replace("__FORMATS_JSON__", _json.dumps(FORMATS))
    return HTMLResponse(html)



def main() -> None:
    log.info("ytgrab → http://%s:%d (salida: %s)", HOST, PORT, OUT_DIR)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
