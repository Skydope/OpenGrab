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
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from config import HOST, OUT_DIR, PORT, _STATIC_DIR
from download import (
    HISTORY,
    JOBS,
    _cleanup_old_workdirs,
    _load_history,
)
from routes import limiter, router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [opengrab] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("opengrab")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Inicialización y tarea de fondo que elimina jobs completados/fallidos
    después de 1 hora."""
    # Startup: filesystem init (evita I/O bloqueante en import time)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_workdirs()
    global HISTORY
    HISTORY[:] = _load_history()

    async def _evict():
        while True:
            await asyncio.sleep(300)
            cutoff = time.time() - 3600
            to_delete = [
                jid
                for jid, j in JOBS.items()
                if j.status in ("done", "error") and j.created < cutoff
            ]
            for jid in to_delete:
                job = JOBS[jid]
                if job.workdir:
                    wd = Path(job.workdir)
                    if wd.exists():
                        try:
                            shutil.rmtree(wd, ignore_errors=True)
                        except OSError:
                            pass
                del JOBS[jid]
            if to_delete:
                log.info("evacuados %d jobs viejos de memoria", len(to_delete))

    task = asyncio.create_task(_evict())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="OpenGrab", lifespan=_lifespan)

# Security headers (complementan los del nginx para bare-metal)
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response

app.add_middleware(_SecurityHeadersMiddleware)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    lambda req, exc: JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas solicitudes. Espera un momento."},
    ),
)

# Static files (CSS, JS)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# API routes
app.include_router(router)


def main() -> None:
    log.info("OpenGrab -> http://%s:%d (salida: %s)", HOST, PORT, OUT_DIR)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
