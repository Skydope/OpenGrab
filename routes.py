from __future__ import annotations

import asyncio
import json as _json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import FORMATS, HISTORY_MAX, MAX_JOBS, OUT_DIR, TOKEN, _STATIC_DIR
from download import (
    HISTORY,
    JOBS,
    _fetch_info,
    _fetch_playlist,
    _looks_like_supported,
    _run_download,
    _sanitize_url,
)
from models import AuthReq, Job, JobReq

log = logging.getLogger("opengrab")

limiter = Limiter(key_func=get_remote_address, default_limits=["30/minute"])

router = APIRouter()


# --------------------------------------------------------------------------- #
# Auth dependency
# --------------------------------------------------------------------------- #
def require_auth(request: Request) -> None:
    """Dependencia de FastAPI que exige token si OPENGRAB_TOKEN esta seteado.
    Acepta: Authorization Bearer, ?token= query param, o cookie opengrab_token."""
    if not TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:] == TOKEN:
        return
    if request.query_params.get("token") == TOKEN:
        return
    if request.cookies.get("opengrab_token") == TOKEN:
        return
    raise HTTPException(
        401, "Token requerido. Usa Authorization: Bearer <token> o ?token=..."
    )


# --------------------------------------------------------------------------- #
# Index HTML
# --------------------------------------------------------------------------- #
try:
    _INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
except (FileNotFoundError, OSError):
    _INDEX_HTML = (
        "<!doctype html><html><body><h1>OpenGrab</h1>"
        "<p>static/index.html not found.</p></body></html>"
    )


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@router.post("/api/auth")
async def api_auth(request: Request, req: AuthReq, response: Response):
    if not TOKEN:
        return {"ok": True}
    if req.token != TOKEN:
        raise HTTPException(401, "Token invalido.")
    response.set_cookie(
        key="opengrab_token",
        value=TOKEN,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
        path="/",
        secure=request.url.scheme == "https",
    )
    return {"ok": True}


@router.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie(key="opengrab_token", path="/")
    return {"ok": True}


@router.get("/api/info")
@limiter.limit("10/minute")
async def api_info(request: Request, url: str, _: None = Depends(require_auth)):
    url = url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(
            400, "URL no soportada. Probá con YouTube, Vimeo, TikTok, X o Instagram."
        )
    try:
        info = await asyncio.to_thread(_fetch_info, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer el video: {exc}")

    dur = info.get("duration") or 0
    raw_formats: list = info.get("formats") or []
    formats: list = []
    for f in raw_formats:
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        fmt: Dict[str, Any] = {
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


@router.get("/api/playlist")
async def api_playlist(url: str, _: None = Depends(require_auth)):
    url = url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(
            400, "URL no soportada. Probá con YouTube, Vimeo, TikTok, X o Instagram."
        )
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer la playlist: {exc}")
    return JSONResponse(info)


@router.post("/api/jobs")
@limiter.limit("5/minute")
async def api_create_job(
    request: Request, req: JobReq, _: None = Depends(require_auth)
):
    url = req.url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(
            400, "URL no soportada. Probá con YouTube, Vimeo, TikTok, X o Instagram."
        )
    if req.quality not in FORMATS:
        raise HTTPException(400, "Calidad inválida.")
    active = sum(
        1
        for j in JOBS.values()
        if j.status in ("queued", "starting", "downloading", "processing")
    )
    if active >= MAX_JOBS:
        raise HTTPException(
            429,
            f"Límite de {MAX_JOBS} descarga(s) simultánea(s). Esperá que termine una.",
        )

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = Job(id=job_id, created=time.time())
    log.info("job %s: creado (%s, %s)", job_id, req.quality, _sanitize_url(req.url))
    loop = asyncio.get_running_loop()
    asyncio.create_task(
        asyncio.to_thread(_run_download, job_id, url, req.quality, loop)
    )
    return {"job_id": job_id}


@router.get("/api/jobs/{job_id}/events")
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

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/jobs/{job_id}/file")
async def api_job_file(job_id: str, _: None = Depends(require_auth)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado.")
    if job.status != "done":
        raise HTTPException(409, "El archivo todavía no está listo.")
    path = job.filepath
    workdir = job.workdir
    if not path or not Path(path).exists():
        raise HTTPException(410, "El archivo ya no está disponible.")
    if not Path(path).resolve().is_relative_to(OUT_DIR):
        raise HTTPException(403, "Acceso denegado.")
    log.info("job %s: sirviendo archivo → %s", job_id, job.filename)

    file_path = Path(path)
    file_size = file_path.stat().st_size
    filename = job.filename or "download"
    media_type = job.mime or "application/octet-stream"

    async def file_iterator():
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
        finally:
            if workdir and Path(workdir).exists():
                try:
                    shutil.rmtree(workdir, ignore_errors=True)
                except OSError:
                    pass
            job.filepath = ""

    return StreamingResponse(
        file_iterator(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(file_size),
        },
    )


@router.get("/api/history")
async def api_history(limit: int = 20, _: None = Depends(require_auth)):
    entries = HISTORY[-max(1, min(limit, HISTORY_MAX)):]
    return JSONResponse(list(reversed(entries)))


@router.get("/health")
async def health():
    active = sum(
        1
        for j in JOBS.values()
        if j.status in ("queued", "starting", "downloading", "processing")
    )
    return {"status": "ok", "jobs_active": active}


@router.get("/")
async def index():
    html = _INDEX_HTML.replace("__AUTH_REQUIRED__", "true" if TOKEN else "false")
    html = html.replace("__FORMATS_JSON__", _json.dumps(FORMATS).replace("</", "<\\/"))
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)
