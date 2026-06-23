from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import secrets
import sys
import time
import urllib.parse
import uuid
from collections.abc import AsyncGenerator, Generator
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
    VERSION,
    _STATIC_DIR,
)
from download import (
    _check_channel_watch,
    _fetch_info,
    _fetch_playlist,
    _is_safe_url,
    _run_download,
    _sanitize_url,
)
from models import AuthReq, BatchReq, ChannelReq, Job, JobReq
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
    return request.app.state.opengrab  # type: ignore[no-any-return]


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
async def api_auth(request: Request, req: AuthReq, response: Response) -> dict[str, bool]:
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
async def api_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(key="opengrab_token", path="/")
    return {"ok": True}


@router.get("/api/info")
@limiter.limit("10/minute")
async def api_info(
    request: Request,
    url: str,
    _: None = Depends(require_auth),
) -> JSONResponse:
    url = url.strip()
    if not _is_safe_url(url):
        raise HTTPException(
            400, "Pega un enlace http(s) valido. Si el sitio no anda, proba "
            "'Actualizar motor' o revisa el formato del link."
        )
    try:
        info = await asyncio.to_thread(_fetch_info, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer el video: {exc}")

    dur = info.get("duration") or 0
    raw_formats: list[dict[str, Any]] = info.get("formats") or []
    formats: list[dict[str, Any]] = []
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
        "site": info.get("extractor_key") or info.get("extractor") or "",
        "formats": formats,
    })


@router.get("/api/playlist")
@limiter.limit("10/minute")
async def api_playlist(
    request: Request,
    url: str,
    _: None = Depends(require_auth),
) -> JSONResponse:
    url = url.strip()
    if not _is_safe_url(url):
        raise HTTPException(
            400, "Pega un enlace http(s) valido. Si el sitio no anda, proba "
            "'Actualizar motor' o revisa el formato del link."
        )
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer la playlist: {exc}")
    return JSONResponse(info)


@router.post("/api/playlist/download")
@limiter.limit("2/minute")
async def api_batch_download(
    request: Request,
    req: BatchReq,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    # Validate quality
    if req.quality not in FORMATS:
        raise HTTPException(400, "Calidad invalida.")
    # Validate each URL
    skipped = []
    valid_urls = []
    for url in req.urls:
        if not _is_safe_url(url.strip()):
            skipped.append({"url": url, "reason": "URL invalida"})
        else:
            valid_urls.append(url.strip())
    # Cap at 100
    if len(valid_urls) > 100:
        for url in valid_urls[100:]:
            skipped.append({"url": url, "reason": "limite de batch (100)"})
        valid_urls = valid_urls[:100]
    # Insert into DB (queued only, NOT state.jobs)
    job_ids = []
    for url in valid_urls:
        job_id = uuid.uuid4().hex[:12]
        state.db.insert_job(job_id, url, req.quality)
        job_ids.append(job_id)
    return {"job_ids": job_ids, "queued": len(job_ids), "skipped": skipped}


@router.get("/api/jobs/batch-status")
async def api_batch_status(
    request: Request,
    ids: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    in_memory = {}
    missing = []
    for job_id in id_list:
        if job_id in state.jobs:
            job = state.jobs[job_id]
            in_memory[job_id] = {
                "job_id": job_id,
                "status": job.status,
                "percent": job.percent,
                "speed": job.speed,
                "eta": job.eta,
                "error": job.error,
                "filename": job.filename,
                "title": job.title,
            }
        else:
            missing.append(job_id)
    # Batch DB query for missing
    db_results = {}
    if missing:
        rows = state.db.get_jobs(missing)
        for row in rows:
            d = dict(row)
            db_results[d["id"]] = {
                "job_id": d["id"],
                "status": d["status"],
                "percent": 100.0 if d["status"] == "done" else 0.0,
                "speed": "",
                "eta": "",
                "error": d.get("error") or "",
                "filename": d.get("filename") or "",
                "title": d.get("title") or "",
            }
    # Combine: memory jobs first, then DB jobs that weren't in memory
    result = list(in_memory.values()) + [v for k, v in db_results.items() if k not in in_memory]
    # Add unknown IDs with empty status
    found_ids = set(in_memory.keys()) | set(db_results.keys())
    for job_id in missing:
        if job_id not in found_ids:
            result.append({
                "job_id": job_id,
                "status": "",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "error": "",
                "filename": "",
                "title": "",
            })
    return JSONResponse(result)


@router.post("/api/jobs")
@limiter.limit("5/minute")
async def api_create_job(
    request: Request,
    req: JobReq,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    url = req.url.strip()
    if not _is_safe_url(url):
        raise HTTPException(
            400, "Pega un enlace http(s) valido. Si el sitio no anda, proba "
            "'Actualizar motor' o revisa el formato del link."
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
    state.db.insert_job(job_id, url, req.quality)
    log.info("job %s: creado (%s, %s)", job_id, req.quality, _sanitize_url(req.url))
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(
        asyncio.to_thread(_run_download, state, job_id, url, req.quality, loop)
    )
    state.running_tasks.add(task)
    task.add_done_callback(state.running_tasks.discard)
    return {"job_id": job_id}


async def _job_events_stream(state: AppState, job_id: str) -> AsyncGenerator[str, None]:
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
            "filepath": job.filepath,
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
) -> StreamingResponse:
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
) -> StreamingResponse:
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

    async def file_iterator() -> AsyncGenerator[bytes, None]:
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
) -> JSONResponse:
    try:
        entries = state.get_history(limit=max(1, min(limit, HISTORY_MAX)))
        return JSONResponse(
            entries,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    except Exception as exc:
        log.exception("api_history: error al leer historial")
        raise HTTPException(500, f"Error al leer historial: {exc}")


@router.post("/api/jobs/{job_id}/open-folder")
async def api_open_folder(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    job = state.jobs.get(job_id)
    if job is None or not job.filepath:
        raise HTTPException(404, "Job no encontrado o sin archivo.")
    folder = str(Path(job.filepath).parent)
    if sys.platform == "win32":
        os.startfile(folder)
    return JSONResponse({"ok": True, "folder": folder})


@router.delete("/api/history/{job_id}")
async def api_delete_history_entry(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    job = state.db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Entrada no encontrada.")
    filepath = job.get("filepath")
    workdir = job.get("workdir")
    if not state.db.delete_job(job_id):
        raise HTTPException(404, "Entrada no encontrada.")
    state.jobs.pop(job_id, None)
    state.job_events.pop(job_id, None)
    log.info("delete_history_entry: borrado job %s de la DB", job_id)
    if filepath or workdir:
        asyncio.create_task(
            asyncio.to_thread(state._secure_delete_files, filepath, workdir)
        )
    return JSONResponse({"ok": True})


@router.delete("/api/history")
@limiter.limit("5/minute")
async def api_clear_history(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    count = await asyncio.to_thread(state.clear_all_history)
    log.info("historial limpiado: %d entradas borradas", count)
    return JSONResponse({"ok": True, "deleted": count})


@router.get("/api/storage")
async def api_storage(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    info = await asyncio.to_thread(state.list_storage)
    return JSONResponse(info)


@router.post("/api/storage/cleanup")
@limiter.limit("5/minute")
async def api_storage_cleanup(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    max_age = max(1, int(body.get("max_age_hours", 24)))
    result = await asyncio.to_thread(state.cleanup_storage, max_age)
    log.info("storage cleanup: %d workdirs, %d bytes liberados",
             result["cleaned"], result["freed_bytes"])
    return JSONResponse(result)


@router.post("/api/storage/cleanup-all")
@limiter.limit("3/minute")
async def api_storage_cleanup_all(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    result = await asyncio.to_thread(state.cleanup_storage_all)
    log.info("storage cleanup-all: %d workdirs, %d bytes liberados",
             result["cleaned"], result["freed_bytes"])
    return JSONResponse(result)


@router.post("/api/engine/update")
@limiter.limit("2/minute")
async def api_engine_update(
    request: Request,
    _: None = Depends(require_auth),
) -> JSONResponse:
    """Fuerza el hot-swap de yt-dlp (botón "Actualizar motor"). La descarga es I/O
    bloqueante → va a un thread para no frenar el event loop. Degrada solo si falla."""
    import engine_update

    result = await asyncio.to_thread(engine_update.check_and_update, True)
    return JSONResponse(result)


# --------------------------------------------------------------------------- #
# Channel management (watch mode)
# --------------------------------------------------------------------------- #
@router.get("/api/channels")
async def api_list_channels(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    channels = state.db.list_channels()
    return JSONResponse(channels)


@router.post("/api/channels")
@limiter.limit("10/minute")
async def api_create_channel(
    request: Request,
    req: ChannelReq,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    cid = state.db.insert_channel(req.url, req.quality, req.interval_minutes)
    return JSONResponse({"id": cid, "url": req.url})


@router.put("/api/channels/{channel_id}")
@limiter.limit("10/minute")
async def api_update_channel(
    request: Request,
    channel_id: int,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    ch = state.db.get_channel(channel_id)
    if ch is None:
        raise HTTPException(404, "Canal no encontrado.")
    body = await request.json()
    updatable = {"title", "quality", "interval_minutes", "enabled"}
    fields = {k: v for k, v in body.items() if k in updatable}
    if fields:
        state.db.update_channel(channel_id, **fields)
    return JSONResponse({"ok": True})


@router.delete("/api/channels/{channel_id}")
@limiter.limit("10/minute")
async def api_delete_channel(
    request: Request,
    channel_id: int,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    if state.db.get_channel(channel_id) is None:
        raise HTTPException(404, "Canal no encontrado.")
    state.db.delete_channel(channel_id)
    return JSONResponse({"ok": True})


@router.post("/api/channels/{channel_id}/check")
@limiter.limit("5/minute")
async def api_check_channel(
    request: Request,
    channel_id: int,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    ch = state.db.get_channel(channel_id)
    if ch is None:
        raise HTTPException(404, "Canal no encontrado.")
    videos = await asyncio.to_thread(_check_channel_watch, state, ch)
    state.db.touch_channel(channel_id)
    return JSONResponse({"ok": True, "new_videos": len(videos), "videos": videos})


@router.get("/api/debug/routes")
async def api_debug_routes(request: Request) -> JSONResponse:
    routes = []
    for r in request.app.routes:
        routes.append({
            "path": getattr(r, "path", str(r)),
            "methods": sorted(m for m in (getattr(r, "methods", None) or set())),
        })
    return JSONResponse(routes)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/")
async def index() -> HTMLResponse:
    html = _INDEX_HTML.replace("__AUTH_REQUIRED__", "true" if TOKEN else "false")
    html = html.replace(
        "__FORMATS_JSON__", _json.dumps(FORMATS).replace("</", "<\\/")
    )
    html = html.replace("__VERSION__", VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
