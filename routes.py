from __future__ import annotations

import asyncio
import configparser
import json as _json
import logging
import os
import re
import secrets
import sys
import time
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from slowapi import Limiter

from config import (
    FORMATS,
    IS_DESKTOP,
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
    _sanitize_url,
)
from models import AuthReq, BatchReq, ChannelReq, Job, JobReq
from state import AppState
from i18n import t

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
        401, t("error.token_required")
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
        raise HTTPException(401, t("error.token_invalid"))
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
    safe, reason = _is_safe_url(url)
    if not safe:
        raise HTTPException(400, t(reason))
    try:
        info = await asyncio.to_thread(_fetch_info, url)
    except Exception as exc:
        raise HTTPException(502, t("error.info_failed", exc=exc))

    dur = info.get("duration") or 0
    raw_formats: list[dict[str, Any]] = info.get("formats") or []
    formats: list[dict[str, Any]] = []
    for f in raw_formats:
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        fmt: dict[str, Any] = {
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
    safe, reason = _is_safe_url(url)
    if not safe:
        raise HTTPException(400, t(reason))
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:
        raise HTTPException(502, t("error.playlist_failed", exc=exc))
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
        raise HTTPException(400, t("error.quality_invalid"))
    # Validate each URL
    skipped = []
    valid_urls = []
    for url in req.urls:
        safe, reason = _is_safe_url(url.strip())
        if not safe:
            skipped.append({"url": url, "reason": reason})
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
        raise HTTPException(400, t(reason))
    if req.quality not in FORMATS:
        raise HTTPException(400, t("error.quality_invalid"))
    max_jobs = state.resolve("max_jobs", 2, int)[0]
    if state.count_active_jobs() >= max_jobs:
        raise HTTPException(
            429,
            t("error.max_jobs", max_jobs=max_jobs),
        )
    max_total_mb = state.resolve("max_total_mb", 0, int)[0]
    if max_total_mb and state.current_usage_bytes() >= max_total_mb * 1024 * 1024:
        raise HTTPException(
            507,
            t("error.storage_full", max_total_mb=max_total_mb),
        )

    job_id = uuid.uuid4().hex[:12]
    state.db.insert_job(job_id, url, req.quality)
    log.info("job %s: creado (%s, %s)", job_id, req.quality, _sanitize_url(req.url))
    state._spawn_download(job_id, url, req.quality)
    return {"job_id": job_id}


@router.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    result = state.cancel_job(job_id)
    if result == "noop":
        raise HTTPException(404, t("error.job_not_found"))
    log.info("job %s: cancelación solicitada (%s)", job_id, result)
    return {"status": result}


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
        raise HTTPException(404, t("error.job_not_found_short"))
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
        raise HTTPException(404, t("error.job_not_found_short"))
    if job.status != "done":
        raise HTTPException(409, t("error.file_not_ready"))
    path = job.filepath
    if not path:
        raise HTTPException(410, t("error.file_gone"))
    resolved = Path(path).resolve()
    allowed = [state.out_dir.resolve()]
    if IS_DESKTOP:
        allowed.append(state.resolve_library_dir())
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise HTTPException(403, t("error.access_denied"))
    if not Path(path).exists():
        raise HTTPException(410, t("error.file_gone"))
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
        history_max = state.resolve("history_max", 500, int)[0]
        entries = state.get_history(limit=max(1, min(limit, history_max)))
        return JSONResponse(
            entries,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    except Exception as exc:
        log.exception("api_history: error al leer historial")
        raise HTTPException(500, t("error.history_read_failed", exc=exc))


@router.post("/api/jobs/{job_id}/open-folder")
async def api_open_folder(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    if not IS_DESKTOP:
        raise HTTPException(409, t("error.folder_desktop_only"))
    job = state.jobs.get(job_id)
    if job is None or not job.filepath:
        raise HTTPException(404, t("error.job_not_found_short"))
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
    except Exception:
        raise HTTPException(400, t("error.json_invalid"))
    dest = body.get("dest", "") if isinstance(body, dict) else ""
    if not isinstance(dest, str) or not dest.strip():
        raise HTTPException(400, t("error.missing_field", field="dest"))
    try:
        target = state.move_job_file(job_id, Path(dest.strip()))
    except ValueError:
        raise HTTPException(409, t("error.file_not_ready"))
    except FileNotFoundError:
        raise HTTPException(410, t("error.file_gone"))
    except NotADirectoryError:
        raise HTTPException(400, t("error.dest_not_dir"))
    except OSError as exc:
        log.warning("api_move_job_file: falló move job %s -> %s: %s", job_id, dest, exc)
        raise HTTPException(500, t("error.move_failed"))
    return JSONResponse({"ok": True, "filepath": str(target)})


@router.delete("/api/history/{job_id}")
async def api_delete_history_entry(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    result = state.delete_history_entry(job_id)
    if result is None:
        raise HTTPException(404, t("error.history_entry_not_found"))
    filepath, workdir = result
    if filepath or workdir:
        task = asyncio.create_task(
            asyncio.to_thread(state._secure_delete_files, filepath, workdir)
        )
        state._track_task(task)
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
# Settings catalog + API
# --------------------------------------------------------------------------- #
# Metadata completa por setting:
#   key → (type, scope, default, description, placeholder, options, tokens, validation)
_FULL_CATALOG: dict[str, tuple[str, str, Any, str, str, Any, Any, Any]] = {
    "max_jobs": (
        "int", "runtime", 2,
        "Descargas simultáneas",
        "2",
        None, None,
        {"min": 1, "max": 20},
    ),
    "max_total_mb": (
        "int", "runtime", 0,
        "Almacenamiento máximo (MB, 0 = sin límite)",
        "0",
        None, None,
        {"min": 0},
    ),
    "max_size_mb": (
        "int", "runtime", 0,
        "Tamaño máximo por archivo (MB, 0 = sin límite)",
        "0",
        None, None,
        {"min": 0},
    ),
    "history_max": (
        "int", "runtime", 500,
        "Entradas máximas en el historial",
        "500",
        None, None,
        {"min": 10, "max": 10000},
    ),
    "quality_default": (
        "string", "runtime", "best",
        "Calidad por defecto para nuevas descargas",
        "best",
        [
            {"value": "best", "label": "best mp4"},
            {"value": "1080p", "label": "1080p"},
            {"value": "720p", "label": "720p"},
            {"value": "480p", "label": "480p"},
            {"value": "audio", "label": "solo audio · mp3"},
        ],
        None, None,
    ),
    "theme": (
        "string", "runtime", "auto",
        "Tema de la interfaz",
        "auto",
        [
            {"value": "dark", "label": "Oscuro"},
            {"value": "light", "label": "Claro"},
            {"value": "auto", "label": "Automático"},
        ],
        None, None,
    ),
    "lang": (
        "string", "runtime", "auto",
        "Idioma de la interfaz",
        "auto",
        [
            {"value": "auto", "label": "Automático"},
            {"value": "es", "label": "Español"},
            {"value": "en", "label": "English"},
        ],
        None, None,
    ),
    "notifications_enabled": (
        "bool", "runtime", False,
        "Notificaciones del navegador (watch mode)",
        "",
        None, None, None,
    ),
    "library_dir": (
        "string", "desktop", "",
        "Directorio donde se guardan las descargas",
        "C:\\Users\\...\\Downloads\\OpenGrab",
        None, None, None,
    ),
    "name_template": (
        "string", "desktop", "{title}",
        "Plantilla para el nombre del archivo descargado",
        "{title}",
        None,
        ["{title}", "{channel}", "{upload_year}", "{upload_date}",
         "{extractor}", "{video_id}", "{resolution}"],
        None,
    ),
}

# Catálogo simple para compatibilidad con resolve() y lookup rápido.
_SETTING_CATALOG: dict[str, tuple[str, str, Any]] = {
    k: (t, s, d) for k, (t, s, d, *_rest) in _FULL_CATALOG.items()
}

# Validadores server-side por setting.
_SETTING_VALIDATORS: dict[str, dict[str, Any]] = {
    key: (val[7] or {}) for key, val in _FULL_CATALOG.items() if val[7]
}
# Settings de tipo select (para validación de valores permitidos).
_SETTING_OPTIONS: dict[str, set[str]] = {
    key: {o["value"] for o in val[5]}
    for key, val in _FULL_CATALOG.items() if val[5]
}

_VALID_QUALITIES = frozenset(FORMATS)
_VALID_THEMES = frozenset({"dark", "light", "auto"})
_VALID_LANGS = frozenset({"es", "en", "auto"})

_NAME_TEMPLATE_TOKENS = frozenset({
    "{title}", "{channel}", "{upload_year}", "{upload_date}",
    "{extractor}", "{video_id}", "{resolution}",
})


def _validate_setting_value(key: str, raw_value: str) -> str | None:
    """Valida un valor para un setting. Devuelve mensaje de error o None."""
    info = _FULL_CATALOG.get(key)
    if info is None:
        return t("error.settings_unknown_key")
    vtype = info[0]

    if vtype == "int":
        try:
            ival = int(raw_value)
        except (ValueError, TypeError):
            return t("error.settings_int_invalid")
        rules = _SETTING_VALIDATORS.get(key, {})
        if "min" in rules and ival < rules["min"]:
            return t("error.settings_min_value", min=rules["min"])
        if "max" in rules and ival > rules["max"]:
            return t("error.settings_max_value", max=rules["max"])
        return None

    if vtype == "bool":
        if str(raw_value).strip().lower() in ("true", "1", "yes"):
            return None
        if str(raw_value).strip().lower() in ("false", "0", "no", ""):
            return None
        return t("error.settings_bool_invalid")

    if vtype == "string":
        sval = str(raw_value)
        # Validar opciones si es un select
        if key in _SETTING_OPTIONS and sval not in _SETTING_OPTIONS[key]:
            return t("error.settings_value_not_allowed", options=sorted(_SETTING_OPTIONS[key]))
        # Validar tokens para name_template
        if key == "name_template":
            tokens = re.findall(r"\{[a-z_]+\}", sval)
            invalid = [t for t in tokens if t not in _NAME_TEMPLATE_TOKENS]
            if invalid:
                return t("error.settings_tokens_invalid", tokens=", ".join(invalid))
        # Validar library_dir: warn si no existe, pero no bloquear
        if key == "library_dir" and sval:
            p = Path(sval)
            if p.exists() and not p.is_dir():
                return t("error.settings_path_not_dir")
        return None

    return None


def _write_setting_to_ini(key: str, value: str) -> bool:
    """Escribe key=value en el ini de OpenGrab. Crea dir+archivo si no existen.

    Devuelve True si se escribió ok o False si falló (e.g. FS read-only).
    """
    try:
        if sys.platform == "win32":
            base = Path(os.environ.get(
                "APPDATA", str(Path.home() / "AppData" / "Roaming")
            ))
        else:
            base = Path(os.environ.get(
                "XDG_CONFIG_HOME", str(Path.home() / ".config")
            ))
        ini_path = Path(os.environ.get(
            "OPENGRAB_CONFIG", str(base / "OpenGrab" / "config.ini")
        ))
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser()
        if ini_path.exists():
            cp.read(ini_path, encoding="utf-8")
        if "opengrab" not in cp:
            cp["opengrab"] = {}
        cp["opengrab"][key] = value
        cp.write(open(ini_path, "w", encoding="utf-8"))
        return True
    except Exception:
        return False


@router.get("/api/settings")
async def api_get_settings(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Devuelve el catálogo completo de settings con valor actual y metadata."""
    catalog: list[dict[str, Any]] = []
    for key, (vtype, scope, default, desc, placeholder,
              options, tokens, validation) in _FULL_CATALOG.items():
        val, origin = state.resolve(key, default, str)
        # Cast para la respuesta
        if vtype == "int":
            try:
                val = int(val)
            except (ValueError, TypeError):
                val = default
        elif vtype == "bool":
            if isinstance(val, str):
                val = val.strip().lower() in ("true", "1", "yes")
            elif isinstance(val, bool):
                pass
            else:
                val = bool(val)

        locked = origin in ("env", "ini")
        entry: dict[str, Any] = {
            "key": key,
            "type": vtype,
            "scope": scope,
            "value": val,
            "default": default,
            "origin": origin,
            "locked": locked,
            "restart_required": False,
            "description": desc,
            "placeholder": placeholder,
        }
        if options:
            entry["options"] = options
        if tokens:
            entry["tokens"] = tokens
        if validation:
            entry["validation"] = validation
        catalog.append(entry)
    return JSONResponse(catalog)


@router.get("/api/settings/defaults")
async def api_get_settings_defaults(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Devuelve los defaults que el frontend necesita para inicializar chips."""
    quality, _origin_q = state.resolve("quality_default", "best", str)
    theme, _origin_t = state.resolve("theme", "auto", str)
    lang, _origin_l = state.resolve("lang", "auto", str)
    notif, _origin_n = state.resolve("notifications_enabled", False, str)
    notif_enabled = (isinstance(notif, bool) and notif) or str(notif).strip().lower() in ("true", "1", "yes")
    return JSONResponse({
        "quality_default": quality,
        "theme": theme,
        "lang": lang,
        "notifications_enabled": notif_enabled,
    })


@router.put("/api/settings")
@router.patch("/api/settings")
@limiter.limit("10/minute")
async def api_update_settings(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Actualiza settings (PUT o PATCH). Keys locked (origin=env/ini) retornan 400."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, t("error.json_invalid"))
    if not isinstance(body, dict):
        raise HTTPException(400, t("error.settings_body_dict"))
    errors: dict[str, str] = {}
    updated: list[str] = []
    for key, raw_value in body.items():
        if key not in _FULL_CATALOG:
            errors[key] = t("error.settings_unknown_key")
            continue
        _, origin = state.resolve(key, _FULL_CATALOG[key][2], str)
        if origin in ("env", "ini"):
            errors[key] = t("error.settings_locked", origin=origin)
            continue
        # Validacion server-side
        str_value = str(raw_value)
        err = _validate_setting_value(key, str_value)
        if err:
            errors[key] = err
            continue
        # Persist: table + ini
        state.db.set_setting(key, str_value)
        _write_setting_to_ini(key, str_value)
        from config import set_ini

        set_ini(key, str_value)
        updated.append(key)
    if errors and not updated:
        raise HTTPException(
            400,
            {"error": t("error.settings_all_failed"), "details": errors},
        )
    return JSONResponse({
        "ok": True,
        "updated": updated,
        "errors": errors if errors else None,
    })


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
        raise HTTPException(404, t("error.canal_not_found"))
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
        raise HTTPException(404, t("error.canal_not_found"))
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
        raise HTTPException(404, t("error.canal_not_found"))
    videos = await asyncio.to_thread(_check_channel_watch, state, ch)
    state.db.touch_channel(channel_id)
    return JSONResponse({"ok": True, "new_videos": len(videos), "videos": videos})


@router.get("/api/debug/routes")
async def api_debug_routes(
    request: Request,
    _: None = Depends(require_auth),
) -> JSONResponse:
    routes = []
    for r in request.app.routes:
        routes.append({
            "path": getattr(r, "path", str(r)),
            "methods": sorted(m for m in (getattr(r, "methods", None) or set())),
        })
    return JSONResponse(routes)


@router.get("/api/metrics")
async def api_metrics(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    counts = state.db.count_jobs_by_status()
    return JSONResponse({
        "version": VERSION,
        "uptime_seconds": round(time.monotonic() - state._start_time, 1),
        "jobs_active": state.count_active_jobs(),
        "jobs_queued": counts.get("queued", 0),
        "jobs_done": counts.get("done", 0),
        "jobs_error": counts.get("error", 0),
        "jobs_interrupted": counts.get("interrupted", 0),
        "jobs_total": sum(counts.values()),
        "usage_bytes": state.current_usage_bytes(),
        "channels_watched": len(state.db.list_channels()),
    })


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
    html = html.replace("__IS_DESKTOP__", "true" if IS_DESKTOP else "false")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
