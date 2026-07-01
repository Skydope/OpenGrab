from __future__ import annotations

from . import limiter, require_auth, get_state, log
from config import FORMATS
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from i18n import t as _t

import asyncio
import uuid
from typing import Any

from download import _fetch_playlist, _is_safe_url, _safe_name
from models import BatchReq
from state import AppState

router = APIRouter()


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
        raise HTTPException(400, _t(reason))
    try:
        info = await asyncio.to_thread(_fetch_playlist, url)
    except Exception as exc:  # yt-dlp: superficie amplia de fallas (1800+ extractors)
        log.exception("api_playlist: yt-dlp falló al resolver %s", url)
        raise HTTPException(502, _t("error.playlist_failed", exc=exc))
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
        raise HTTPException(400, _t("error.quality_invalid"))
    # Validate each URL
    skipped = []
    valid_urls = []
    for url in req.urls:
        safe, reason = _is_safe_url(url.strip())
        if not safe:
            skipped.append({"url": url, "reason": _t(reason)})
        else:
            valid_urls.append(url.strip())
    # Cap at 100
    if len(valid_urls) > 100:
        for url in valid_urls[100:]:
            skipped.append({"url": url, "reason": "limite de batch (100)"})
        valid_urls = valid_urls[:100]
    # Si el usuario pidió guardar en subcarpeta, sanitizamos el título de la
    # playlist UNA sola vez (mismo nombre de carpeta para todos los jobs del
    # batch). _safe_name ya defiende contra path traversal / chars ilegales.
    playlist_subdir = (
        _safe_name(req.playlist_title or "playlist")
        if req.save_subfolder
        else None
    )
    # Insert into DB (queued only, NOT state.jobs)
    job_ids = []
    for url in valid_urls:
        job_id = uuid.uuid4().hex[:12]
        state.db.insert_job(job_id, url, req.quality, playlist_subdir=playlist_subdir)
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
