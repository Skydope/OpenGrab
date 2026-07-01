from __future__ import annotations

import sqlite3

from . import limiter, require_auth, get_state, log
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from i18n import t as _t
from state import AppState

import asyncio

router = APIRouter()


@router.get("/api/history")
async def api_history(
    limit: int = 20,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    try:
        history_max = state.resolve("history_max", 500, int)[0]
        entries = state.history.get_history(limit=max(1, min(limit, history_max)))
        return JSONResponse(
            entries,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    except (sqlite3.Error, TypeError) as exc:
        log.exception("api_history: error al leer historial")
        raise HTTPException(500, _t("error.history_read_failed", exc=exc))


@router.delete("/api/history/{job_id}")
async def api_delete_history_entry(
    job_id: str,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    result = state.history.delete_history_entry(job_id)
    if result is None:
        raise HTTPException(404, _t("error.history_entry_not_found"))
    filepath, workdir = result
    if filepath or workdir:
        task = asyncio.create_task(
            asyncio.to_thread(state.history._secure_delete_files, filepath, workdir)
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
    count = await asyncio.to_thread(state.history.clear_all_history)
    log.info("historial limpiado: %d entradas borradas", count)
    return JSONResponse({"ok": True, "deleted": count})
