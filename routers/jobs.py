from __future__ import annotations

from . import limiter, require_auth, get_state, log
from config import IS_DESKTOP
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from i18n import t as _t

import asyncio
import json as _json
import os
import re
import sys
import time
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from download import _sanitize_url, _is_safe_url
from models import Job, JobReq
from config import FORMATS
from state import AppState

router = APIRouter()

_ACTIVE_STATUSES = ("queued", "starting", "downloading", "processing")


def _serialize_job(job: Job) -> dict[str, Any]:
    """Vista de un job para el frontend. Mismo contrato de campos que el SSE
    (status/percent/speed/eta/note/filename/filepath/error) más lo necesario
    para renderizar una tarjeta y reconectar el stream (id/title/created/
    finished/downloaded/total)."""
    return {
        "id": job.id,
        "status": job.status,
        "percent": job.percent,
        "speed": job.speed,
        "eta": job.eta,
        "note": job.note,
        "title": job.title,
        "filename": job.filename,
        "filepath": job.filepath,
        "mime": job.mime,
        "error": job.error,
        "created": job.created,
        "finished": job.finished,
        "downloaded": job.downloaded,
        "total": job.total,
        "incognito": job.incognito,
    }


@router.get("/api/jobs")
async def api_list_jobs(
    recent: float = 900.0,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Lista los jobs vivos en memoria para rehidratar la UI al reabrir.

    Incluye SIEMPRE los jobs en vuelo (queued/starting/downloading/processing)
    y los terminados (done/error) cuyo ``finished`` cae dentro de ``recent``
    segundos — esos se muestran como tarjeta 'listo/error'; los más viejos ya
    viven en el Historial. ``recent=0`` devuelve solo los activos.

    Orden: activos primero por antigüedad (created asc), luego terminados por
    fin más reciente (finished desc)."""
    now = time.time()
    active: list[dict[str, Any]] = []
    finished: list[dict[str, Any]] = []
    for job in state.jobs.values():
        if job.status in _ACTIVE_STATUSES:
            active.append(_serialize_job(job))
        elif recent > 0 and job.finished and (now - job.finished) <= recent:
            finished.append(_serialize_job(job))
    active.sort(key=lambda j: j["created"])
    finished.sort(key=lambda j: j["finished"], reverse=True)
    # Jobs encolados en DB que todavía no se spawnearon (esperando slot en
    # dispatch_loop): no están en state.jobs pero deben verse para poder
    # cancelarlos desde la cola.
    in_mem = set(state.jobs.keys())
    queued_db: list[dict[str, Any]] = []
    for row in state.db.get_queued(limit=1000):
        if row["id"] in in_mem:
            continue
        queued_db.append({
            "id": row["id"], "status": "queued", "percent": 0.0,
            "speed": "", "eta": "", "note": "", "title": row.get("title") or "",
            "filename": "", "filepath": "", "mime": "", "error": "",
            "created": row.get("created") or 0.0, "finished": 0.0,
            "downloaded": 0, "total": 0,
        })
    queued_db.sort(key=lambda j: j["created"])
    return JSONResponse(
        active + queued_db + finished,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.post("/api/jobs")
@limiter.limit("5/minute")
async def api_create_job(
    request: Request,
    req: JobReq,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    url = req.url.strip()
    safe, reason = _is_safe_url(url)
    if not safe:
        raise HTTPException(400, _t(reason))
    if req.quality not in FORMATS:
        raise HTTPException(400, _t("error.quality_invalid"))
    incognito_dir: str | None = None
    if req.incognito:
        # En incógnito el archivo NO va a out_dir/library_dir ni al historial:
        # se entrega a una carpeta elegida por el usuario, así que es obligatoria.
        raw = (req.incognito_dir or "").strip()
        if not raw:
            raise HTTPException(400, _t("error.incognito_dir_required"))
        incognito_dir = raw
    max_jobs = state.resolve("max_jobs", 2, int)[0]
    if state.count_active_jobs() >= max_jobs:
        raise HTTPException(
            429,
            _t("error.max_jobs", max_jobs=max_jobs),
        )
    max_total_mb = state.resolve("max_total_mb", 0, int)[0]
    if max_total_mb and state.storage.current_usage_bytes() >= max_total_mb * 1024 * 1024:
        raise HTTPException(
            507,
            _t("error.storage_full", max_total_mb=max_total_mb),
        )

    job_id = uuid.uuid4().hex[:12]
    state.db.insert_job(job_id, url, req.quality, incognito=req.incognito)
    if req.incognito:
        log.info("job %s: creado (incógnito, %s)", job_id, req.quality)
    else:
        log.info("job %s: creado (%s, %s)", job_id, req.quality, _sanitize_url(req.url))
    state._spawn_download(job_id, url, req.quality,
                          subs=req.subs, thumb=req.thumb,
                          infojson=req.infojson, incognito=req.incognito,
                          incognito_dir=incognito_dir)
    return {"job_id": job_id}


@router.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    result = state.cancel_job(job_id)
    if result == "noop":
        raise HTTPException(404, _t("error.job_not_found"))
    log.info("job %s: cancelación solicitada (%s)", job_id, result)
    return {"status": result}


@router.post("/api/jobs/{job_id}/dismiss")
async def api_dismiss_job(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> dict[str, bool]:
    """Descarta un job terminado de la vista de sesion.

    Remueve el job de memoria para que GET /api/jobs no lo devuelva al
    rehidratar. No borra la DB ni los archivos — el job sigue en Historial."""
    ok = state.dismiss_job_from_view(job_id)
    return {"ok": ok}


async def _job_events_stream(state: AppState, job_id: str) -> AsyncGenerator[str, None]:
    last = None
    while True:
        job = state.jobs.get(job_id)
        if job is None:
            break
        event = state.job_events.get(job_id)
        if event is None:
            break
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
        except TimeoutError:
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
        if job.status in ("done", "error", "cancelled"):
            break


@router.get("/api/jobs/{job_id}/events")
async def api_job_events(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> StreamingResponse:
    if job_id not in state.jobs:
        raise HTTPException(404, _t("error.job_not_found_short"))
    return StreamingResponse(
        _job_events_stream(state, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/jobs/{job_id}/extras")
async def api_job_extras(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Devuelve archivos extra (subs, thumb, info json) del workdir del job."""
    job = state.jobs.get(job_id)
    if not job or not job.workdir:
        return JSONResponse([])
    wd = Path(job.workdir)
    if not wd.exists():
        return JSONResponse([])
    extras: list[dict[str, Any]] = []
    for f in sorted(wd.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in (".srt", ".vtt"):
            extras.append({"filename": f.name, "type": "subs", "size": f.stat().st_size})
        elif ext in (".jpg", ".webp", ".png"):
            extras.append({"filename": f.name, "type": "thumb", "size": f.stat().st_size})
        elif f.name.endswith(".info.json"):
            extras.append({"filename": f.name, "type": "infojson", "size": f.stat().st_size})
    return JSONResponse(extras)


@router.get("/api/jobs/{job_id}/file/{filename}")
async def api_job_file_specific(
    job_id: str,
    filename: str,
    state: AppState = Depends(get_state),
) -> FileResponse:
    """Sirve un archivo extra específico del workdir del job (subs, thumb, info json)."""
    job = state.jobs.get(job_id)
    if not job or not job.workdir:
        raise HTTPException(404, _t("error.job_not_found_short"))
    workdir = Path(job.workdir)
    safe_name = os.path.basename(filename)
    filepath = (workdir / safe_name).resolve()
    if not filepath.is_relative_to(workdir.resolve()):
        raise HTTPException(403, _t("error.access_denied"))
    if not filepath.is_file():
        raise HTTPException(404, _t("error.file_gone"))
    return FileResponse(
        str(filepath),
        filename=safe_name,
        media_type="application/octet-stream",
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
        raise HTTPException(404, _t("error.job_not_found_short"))
    if job.status != "done":
        raise HTTPException(409, _t("error.file_not_ready"))
    path = job.filepath
    if not path:
        raise HTTPException(410, _t("error.file_gone"))
    resolved = Path(path).resolve()
    allowed = [state.out_dir.resolve()]
    if IS_DESKTOP:
        allowed.append(state.resolve_library_dir())
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise HTTPException(403, _t("error.access_denied"))
    if not Path(path).exists():
        raise HTTPException(410, _t("error.file_gone"))
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


@router.post("/api/jobs/{job_id}/open-folder")
async def api_open_folder(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    if not IS_DESKTOP:
        raise HTTPException(409, _t("error.folder_desktop_only"))
    job = state.jobs.get(job_id)
    if job is None or not job.filepath:
        raise HTTPException(404, _t("error.job_not_found_short"))
    folder = str(Path(job.filepath).parent)
    if sys.platform == "win32":
        os.startfile(folder)
    elif sys.platform == "darwin":
        import subprocess

        try:
            subprocess.run(["open", folder], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            log.warning("api_open_folder: falló open %s", folder)
    else:
        import subprocess

        try:
            subprocess.run(["xdg-open", folder], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            log.warning("api_open_folder: falló xdg-open %s", folder)
    return JSONResponse({"ok": True, "folder": folder})


@router.post("/api/jobs/{job_id}/move")
async def api_move_job_file(
    job_id: str,
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Mueve el archivo de un job ``done`` a un directorio del servidor.

    Body: ``{"dest": "<ruta del servidor>"}``. Respalda al botón "Guardar en…":
    el archivo ya vive en el FS del servidor y el usuario elige otra carpeta.
    Pensado para modo desktop (server == cliente). El directorio se crea si no
    existe. Devuelve la ruta destino final.
    """
    try:
        body = await request.json()
    except _json.JSONDecodeError:
        raise HTTPException(400, _t("error.json_invalid"))
    dest = body.get("dest", "") if isinstance(body, dict) else ""
    if not isinstance(dest, str) or not dest.strip():
        raise HTTPException(400, _t("error.missing_field", field="dest"))
    try:
        target = state.library.move_job_file(job_id, Path(dest.strip()))
    except ValueError:
        raise HTTPException(409, _t("error.file_not_ready"))
    except FileNotFoundError:
        raise HTTPException(410, _t("error.file_gone"))
    except NotADirectoryError:
        raise HTTPException(400, _t("error.dest_not_dir"))
    except OSError as exc:
        log.warning("api_move_job_file: falló move job %s -> %s: %s", job_id, dest, exc)
        raise HTTPException(500, _t("error.move_failed"))
    return JSONResponse({"ok": True, "filepath": str(target)})
