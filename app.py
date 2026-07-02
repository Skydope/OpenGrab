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
import inspect
import logging
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from secure_delete import wipe_workdir

# Fix DeprecationWarning de slowapi en Python 3.14+:
# asyncio.iscoroutinefunction está deprecado; inspect.iscoroutinefunction es el reemplazo.
try:
    asyncio.iscoroutinefunction = inspect.iscoroutinefunction  # type: ignore[assignment]
except AttributeError:
    pass  # Python 3.16+: el atributo fue removido; slowapi ya debería tener fix.

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import (
    DB_PATH,
    HOST,
    LOG_FORMAT,
    LOG_LEVEL,
    OUT_DIR,
    PORT,
    TOKEN,
    TOKEN_WAS_GENERATED,
    _STATIC_DIR,
)
from db import Database
from logging_setup import configure_logging
from routers import limiter
from routers import system, settings, jobs, playlist, history, storage, channels, engine, backup
from state import AppState

log = logging.getLogger("opengrab")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = Database(DB_PATH)
    state = AppState(db, OUT_DIR)
    state.storage.cleanup_old_workdirs()

    recon = db.reconcile_startup()
    for j in recon["interrupted"]:
        wd = j.get("workdir")
        if wd:
            try:
                shutil.rmtree(wd, ignore_errors=True)
            except OSError:
                pass
    # Jobs incógnito huérfanos: la fila ya se borró en reconcile_startup. Acá
    # eliminamos su residuo parcial en disco con secure-wipe forzado (el punto
    # del modo incógnito es no dejar rastro, sin depender del flag global).
    for j in recon.get("incognito_dropped", []):
        wd = j.get("workdir")
        if wd:
            try:
                wipe_workdir(wd, force=True)
            except OSError:
                pass
    if recon["requeued"] or recon["interrupted"] or recon.get("incognito_dropped"):
        log.info(
            "reconcile arranque: %d requeued, %d interrupted, %d incógnito descartados",
            len(recon["requeued"]), len(recon["interrupted"]),
            len(recon.get("incognito_dropped", [])),
            extra={
                "requeued": len(recon["requeued"]),
                "interrupted": len(recon["interrupted"]),
                "incognito_dropped": len(recon.get("incognito_dropped", [])),
            },
        )

    db.prune_history(keep=state.resolve("history_max", 500, int)[0])

    import yt_dlp  # type: ignore[import-untyped]
    from metrics import ytdlp_version

    version_str = getattr(getattr(yt_dlp, "version", None), "__version__", "unknown")
    ytdlp_version.info({"version": version_str})

    _app.state.opengrab = state

    task = asyncio.create_task(state.storage.evict_loop())
    watch_task = asyncio.create_task(state.watch_loop())
    dispatch_task = asyncio.create_task(state.dispatch_loop())
    yield
    task.cancel()
    watch_task.cancel()
    dispatch_task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    try:
        await watch_task
    except asyncio.CancelledError:
        pass
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass
    tasks = list(state.running_tasks)
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    db.close()


app = FastAPI(title="OpenGrab", lifespan=_lifespan)


# --------------------------------------------------------------------------- #
# Middleware (ASGI puro)
# --------------------------------------------------------------------------- #
# Migrados desde BaseHTTPMiddleware deliberadamente:
# - BaseHTTPMiddleware envuelve cada request en una task extra + streams
#   memory, con overhead por-request y problemas conocidos con
#   StreamingResponse (nuestro SSE de larga vida) ante desconexión del cliente.
# - Los ContextVar seteados en ASGI puro corren en la MISMA task que el
#   endpoint (propagación garantizada); BaseHTTPMiddleware dependía de que la
#   task hija heredara el contexto.
# Contrato ASGI: solo tocamos scope/mensajes, sin materializar Request/Response
# salvo helpers baratos que no consumen el body.


class _SecurityHeadersMiddleware:
    _HEADERS: tuple[tuple[str, str], ...] = (
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._HEADERS:
                    if name not in headers:  # setdefault: respetar overrides del endpoint
                        headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _LanguageMiddleware:
    """Resuelve el idioma del request: cookie > Accept-Language > default 'es'.

    El frontend persiste la preferencia en la cookie ``opengrab_lang``.
    Para desktop mode (pywebview), el tray lee el setting ``lang`` del ini.
    ``set_lang`` usa ContextVar: en ASGI puro corre en la misma task que el
    endpoint, así la propagación del contexto está garantizada.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from i18n import set_lang, detect_lang

        # Request(scope) es un wrapper barato: leer cookies/headers no toca
        # el body ni consume receive.
        request = Request(scope)
        cookie_lang = request.cookies.get("opengrab_lang", "").strip()
        if cookie_lang in ("es", "en"):
            set_lang(cookie_lang)
        else:
            set_lang(detect_lang(request.headers.get("accept-language", "")))
        await self.app(scope, receive, send)


class _RequestLoggingMiddleware:
    """Log estructurado + métrica Prometheus por request.

    Captura el status en ``http.response.start`` y emite al completar la
    respuesta. Para SSE, esto significa loguear al CERRAR el stream (no al
    abrirlo), con la duración total de la conexión — coherente con lo que
    medía la versión BaseHTTPMiddleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.monotonic()
        status_code = 500  # si la app explota antes de response.start

        async def send_and_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_and_capture)
        finally:
            method = scope.get("method", "?")
            path = scope.get("path", "?")
            if path != "/health":
                dur_ms = (time.monotonic() - t0) * 1000
                log.info(
                    "%s %s %d %.0fms",
                    method, path, status_code, dur_ms,
                    extra={
                        "method": method,
                        "path": path,
                        "status": status_code,
                        "duration_ms": round(dur_ms, 1),
                    },
                )
            from metrics import http_requests

            # El router setea scope["route"] al matchear: para cuando la
            # respuesta terminó, ya está disponible (mismo dict de scope).
            route = scope.get("route")
            endpoint = route.path if route else path
            http_requests.labels(
                endpoint=endpoint,
                method=method,
                status_code=str(status_code),
            ).inc()


# Orden de add_middleware: el último agregado queda más AFUERA. Se preserva el
# orden original: logging (externo) → language → security headers (interno).
app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_LanguageMiddleware)
app.add_middleware(_RequestLoggingMiddleware)


def _rate_limit_handler(_req: Request, _exc: Exception) -> JSONResponse:
    from i18n import t

    return JSONResponse(
        status_code=429,
        content={"detail": t("error.rate_limit")},
    )


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(system.router)
app.include_router(settings.router)
app.include_router(jobs.router)
app.include_router(playlist.router)
app.include_router(history.router)
app.include_router(storage.router)
app.include_router(channels.router)
app.include_router(engine.router)
app.include_router(backup.router)


def main() -> None:
    configure_logging(LOG_FORMAT, LOG_LEVEL)
    log.info("OpenGrab -> http://%s:%d (salida: %s)", HOST, PORT, OUT_DIR)
    if TOKEN_WAS_GENERATED:
        log.info("Auth: token autogenerado = %s", TOKEN)
    else:
        log.info("Auth: usando token de OPENGRAB_TOKEN")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
