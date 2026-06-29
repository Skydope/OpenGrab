from __future__ import annotations

from . import limiter, require_auth, get_state, log
from state import AppState
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import asyncio

router = APIRouter()


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
