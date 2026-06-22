#!/usr/bin/env python3
"""
OpenGrab — self-hosted YouTube downloader (yt-dlp wrapper).

Paste a URL, get an MP4 (or MP3). Backend is FastAPI + yt-dlp + ffmpeg.
Frontend is vanilla HTML/CSS/JS served from static/. Designed for homelab/LAN.

Usage:
    pip install -r requirements.txt
    python app.py
    # -> http://127.0.0.1:8800

Environment:
    OPENGRAB_HOST   (default 127.0.0.1)
    OPENGRAB_PORT   (default 8800)
    OPENGRAB_DIR    (default ./downloads)
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from config import DB_PATH, HISTORY_FILE, HISTORY_MAX, HOST, OUT_DIR, PORT, TOKEN, TOKEN_WAS_GENERATED, _STATIC_DIR
from db import Database
from routes import limiter, router
from state import AppState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [opengrab] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("opengrab")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = Database(DB_PATH)
    state = AppState(db, OUT_DIR, history_max=HISTORY_MAX)
    state.cleanup_old_workdirs()

    interrupted = db.mark_interrupted()
    for j in interrupted:
        wd = j.get("workdir")
        if wd:
            try:
                shutil.rmtree(wd, ignore_errors=True)
            except OSError:
                pass

    if HISTORY_FILE.exists():
        try:
            entries = _json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            imported = db.import_history_json(entries)
            if imported:
                log.info("Migrados %d jobs del history.json a SQLite", imported)
        except (OSError, _json.JSONDecodeError):
            pass

    db.prune_history(keep=HISTORY_MAX)

    _app.state.opengrab = state

    task = asyncio.create_task(state.evict_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    db.close()


app = FastAPI(title="OpenGrab", lifespan=_lifespan)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response = cast(Response, await call_next(request))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response


app.add_middleware(_SecurityHeadersMiddleware)


def _rate_limit_handler(_req: Request, _exc: Exception) -> JSONResponse:
    resp = JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas solicitudes. Espera un momento."},
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    return resp


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(router)


def main() -> None:
    log.info("OpenGrab -> http://%s:%d (salida: %s)", HOST, PORT, OUT_DIR)
    if TOKEN_WAS_GENERATED:
        log.info("Auth: token autogenerado = %s", TOKEN)
    else:
        log.info("Auth: usando token de OPENGRAB_TOKEN")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
