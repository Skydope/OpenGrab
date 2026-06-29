from __future__ import annotations

from . import limiter, require_auth, get_state, _INDEX_HTML
from config import FORMATS, IS_DESKTOP, VERSION
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from i18n import t as _t

import asyncio
import json as _json
import time
from typing import Any

from download import _fetch_info, _is_safe_url
from models import AuthReq
from state import AppState

router = APIRouter()


@router.post("/api/auth")
async def api_auth(request: Request, req: AuthReq, response: Response) -> dict[str, bool]:
    from config import TOKEN
    if not TOKEN:
        return {"ok": True}
    if req.token != TOKEN:
        raise HTTPException(401, _t("error.token_invalid"))
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
        raise HTTPException(400, _t(reason))
    try:
        info = await asyncio.to_thread(_fetch_info, url)
    except Exception as exc:
        raise HTTPException(502, _t("error.info_failed", exc=exc))

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
    from config import TOKEN
    html = _INDEX_HTML.replace("__AUTH_REQUIRED__", "true" if TOKEN else "false")
    html = html.replace(
        "__FORMATS_JSON__", _json.dumps(FORMATS).replace("</", "<\\/")
    )
    html = html.replace("__VERSION__", VERSION)
    html = html.replace("__IS_DESKTOP__", "true" if IS_DESKTOP else "false")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
