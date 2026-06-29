from __future__ import annotations

from . import limiter, require_auth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import asyncio

router = APIRouter()


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
