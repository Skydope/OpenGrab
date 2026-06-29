from __future__ import annotations

from . import limiter, require_auth, get_state
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from i18n import t as _t

import asyncio

from download import _check_channel_watch
from models import ChannelReq
from state import AppState

router = APIRouter()


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
        raise HTTPException(404, _t("error.canal_not_found"))
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
        raise HTTPException(404, _t("error.canal_not_found"))
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
        raise HTTPException(404, _t("error.canal_not_found"))
    videos = await asyncio.to_thread(_check_channel_watch, state, ch)
    state.db.touch_channel(channel_id)
    return JSONResponse({"ok": True, "new_videos": len(videos), "videos": videos})
