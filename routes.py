from __future__ import annotations

import asyncio
import json as _json
import logging
import re
import secrets
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from slowapi import Limiter

from config import (
    FORMATS,
    HISTORY_MAX,
    MAX_JOBS,
    MAX_TOTAL_MB,
    TOKEN,
    TRUST_XFF,
    _STATIC_DIR,
)
from download import (
    _fetch_info,
    _fetch_playlist,
    _looks_like_supported,
    _run_download,
    _sanitize_url,
)
from models import AuthReq, Job, JobReq
from state import AppState

log = logging.getLogger("opengrab")

def _client_key(request: Request) -> str:
    if TRUST_XFF:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


limiter = Limiter(key_func=_client_key, default_limits=["30/minute"])

router = APIRouter()


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #
def get_state(request: Request) -> AppState:
    return request.app.state.opengrab


def require_auth(request: Request) -> None:
    if not TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], TOKEN):
        return
    if secrets.compare_digest(request.query_params.get("token", ""), TOKEN):
        return
    if secrets.compare_digest(request.cookies.get("opengrab_token", ""), TOKEN):
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
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    response.set_cookie(
        key="opengrab_token",
        value=TOKEN,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
        path="/",
        secure=scheme == "https",
    )
    return {"ok": True}


@router.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie(key="opengrab_token", path="/")
    return {"ok": True}


@router.get("/api/info")
@limiter.limit("10/minute")
async def api_info(
    request: Request,
    url: str,
    _: None = Depends(require_auth),
):
    url = url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(
            400, "URL no soportada. Proba con YouTube, Vimeo, TikTok, X o Instagram."
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
    formats.sort(
        key=lambda x: (0 if x["filesize"] is not None else 1, -(x["tbr"] or 0))
    )
    formats = formats[:20]
    return JSONResponse({
        "title": info.get("title", "—"),
        "channel": info.get("uploader") or info.get("channel") or "—",
        "duration": dur,
        "duration_str": time.strftime("%H:%M:%S", time.gmtime(dur)) if dur else "—",
        "thumbnail": info.get("thumbnail"),
        "view_count": info.get("view_count") or 0,
        "formats": formats,
    })


@router.get("/api/playlist")
@limiter.limit("10/minute")
async def api_playlist(
    request: Request,
    url: str,
    _: None = Depends(require_auth),
):
    url = url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(
            400, "URL no soportada. Proba con YouTube, Vimeo, TikTok, X o Instagram."
        )
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer la playlist: {exc}")
    return JSONResponse(info)


@router.post("/api/jobs")
@limiter.limit("5/minute")
async def api_create_job(
    request: Request,
    req: JobReq,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
):
    url = req.url.strip()
    if not _looks_like_supported(url):
        raise HTTPException(
            400, "URL no soportada. Proba con YouTube, Vimeo, TikTok, X o Instagram."
        )
    if req.quality not in FORMATS:
        raise HTTPException(400, "Calidad invalida.")
    if state.count_active_jobs() >= MAX_JOBS:
        raise HTTPException(
            429,
            f"Limite de {MAX_JOBS} descarga(s) simultanea(s). Espera que termine una.",
        )
    if MAX_TOTAL_MB and state.current_usage_bytes() >= MAX_TOTAL_MB * 1024 * 1024:
        raise HTTPException(
            507,
            f"Almacenamiento lleno (limite {MAX_TOTAL_MB} MB). "
            "Borra descargas anteriores antes de seguir.",
        )

    job_id = uuid.uuid4().hex[:12]
    state.jobs[job_id] = Job(id=job_id, created=time.time())
    state.job_events[job_id] = asyncio.Event()
    log.info("job %s: creado (%s, %s)", job_id, req.quality, _sanitize_url(req.url))
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(
        asyncio.to_thread(_run_download, state, job_id, url, req.quality, loop)
    )
    state.running_tasks.add(task)
    task.add_done_callback(state.running_tasks.discard)
    return {"job_id": job_id}


async def _job_events_stream(state: AppState, job_id: str):
    last = None
    while True:
        job = state.jobs.get(job_id)
        if job is None:
            break
        event = state.job_events.get(job_id)
        if event is None:
            break
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


@router.get("/api/jobs/{job_id}/events")
async def api_job_events(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
):
    if job_id not in state.jobs:
        raise HTTPException(404, "Job no encontrado.")
    return StreamingResponse(
        _job_events_stream(state, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _make_content_disposition(filename: str) -> str:
    safe = re.sub(r"[\x00-\x1f\x7f\\]", "", filename)
    ascii_fallback = (
        safe.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    )
    if not ascii_fallback:
        ascii_fallback = "download"
    fallback = ascii_fallback.replace("\\", "\\\\").replace('"', '\\"')
    encoded = urllib.parse.quote(safe, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


@router.get("/api/jobs/{job_id}/file")
async def api_job_file(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
):
    job = state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado.")
    if job.status != "done":
        raise HTTPException(409, "El archivo todavia no esta listo.")
    path = job.filepath
    if not path:
        raise HTTPException(410, "El archivo ya no esta disponible.")
    if not Path(path).resolve().is_relative_to(state.out_dir):
        raise HTTPException(403, "Acceso denegado.")
    if not Path(path).exists():
        raise HTTPException(410, "El archivo ya no esta disponible.")
    log.info("job %s: sirviendo archivo -> %s", job_id, job.filename)

    file_path = Path(path)
    file_size = file_path.stat().st_size
    media_type = job.mime or "application/octet-stream"

    async def file_iterator():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        file_iterator(),
        media_type=media_type,
        headers={
            "Content-Disposition": _make_content_disposition(
                job.filename or "download"
            ),
            "Content-Length": str(file_size),
        },
    )


@router.get("/api/history")
async def api_history(
    limit: int = 20,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
):
    entries = state.history[-max(1, min(limit, HISTORY_MAX)) :]
    return JSONResponse(list(reversed(entries)))


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def index():
    html = _INDEX_HTML.replace("__AUTH_REQUIRED__", "true" if TOKEN else "false")
    html = html.replace(
        "__FORMATS_JSON__", _json.dumps(FORMATS).replace("</", "<\\/")
    )
    return HTMLResponse(html)
