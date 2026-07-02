"""Export e import de backup JSON (settings, history, channels)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from i18n import t as _t
from state import AppState

from . import limiter, require_auth, get_state

router = APIRouter()


@router.get("/api/backup/export")
@limiter.limit("5/minute")
async def api_backup_export(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Exporta settings, history y channels como JSON."""
    settings = state.db.get_all_settings()
    history = state.db.get_history(limit=10000)
    channels = state.db.list_channels()
    return JSONResponse({
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "settings": settings,
        "history": history,
        "channels": channels,
    })


@router.post("/api/backup/import")
@limiter.limit("2/minute")
async def api_backup_import(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Importa backup. Mergea settings (respeta locked env/ini).
    Inserta history como done. Canales disabled por seguridad."""
    try:
        body: dict[str, Any] = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, _t("error.json_invalid"))

    if not isinstance(body, dict) or body.get("version") != 1:
        raise HTTPException(400, _t("error.backup_version_unsupported"))

    imported: dict[str, int] = {"settings": 0, "history": 0, "channels": 0}
    errors: list[str] = []

    # Settings: merge sin sobrescribir locked (env/ini).
    for key, val in (body.get("settings") or {}).items():
        # Usamos el catálogo como autoridad para el default.
        # Si la key no existe en el catálogo, la salteamos.
        from .settings import _SETTING_CATALOG
        info = _SETTING_CATALOG.get(key)
        if info is None:
            continue
        default: Any = info[2]
        _, origin = state.resolve(key, default, str)
        if origin not in ("env", "ini"):
            state.db.set_setting(key, str(val))
            imported["settings"] += 1

    # History: insertar como done (sin descargar).
    for entry in (body.get("history") or []):
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id") or entry.get("job_id")
        if not eid:
            continue
        try:
            # Si ya existe, saltear.
            existing = state.db.get_job(eid)
            if existing:
                continue
            state.db.insert_job(
                eid,
                entry.get("url", ""),
                entry.get("quality", "best"),
                # created NO es columna actualizable (whitelist _UPDATABLE):
                # se fija en el INSERT. Pasarla a update_job lanzaba ValueError
                # y hacia fallar la importacion de CADA entrada de history.
                created=entry.get("created") or None,
            )
            state.db.update_job(
                eid,
                status="done",
                title=entry.get("title", ""),
                filename=entry.get("filename", ""),
                filepath=entry.get("filepath", ""),
                mime=entry.get("mime", ""),
                size=entry.get("size", 0),
                thumbnail=entry.get("thumbnail", ""),
                error=entry.get("error", ""),
                extractor=entry.get("extractor", ""),
                video_id=entry.get("video_id", ""),
                workdir=entry.get("workdir", ""),
                completed=entry.get("completed", 0),
            )
            imported["history"] += 1
        except (sqlite3.Error, ValueError):
            errors.append(str(entry.get("title") or entry.get("url") or eid)[:80])

    # Channels: insertar disabled (seguridad: no arrancar watch auto).
    for ch in (body.get("channels") or []):
        if not isinstance(ch, dict):
            continue
        try:
            state.db.insert_channel(
                ch.get("url", ""),
                ch.get("quality", "best"),
                ch.get("interval_minutes", 60),
            )
            imported["channels"] += 1
        except sqlite3.Error:
            errors.append(str(ch.get("url", ""))[:80])

    return JSONResponse({
        "ok": True,
        "imported": imported,
        "errors": errors if errors else None,
    })
