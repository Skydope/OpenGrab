from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AuthReq(BaseModel):
    token: str


class JobReq(BaseModel):
    url: str
    quality: str = "best"
    subs: bool = False
    thumb: bool = False
    infojson: bool = False
    incognito: bool = False
    incognito_dir: str | None = None
    # Rango de clip en segundos (feature Clips). Ambos o ninguno; la
    # validación de consistencia vive en api_create_job.
    clip_start: float | None = None
    clip_end: float | None = None
    # True = corte exacto (re-encode); False = corte al keyframe (-c copy).
    # Solo tiene efecto si hay clip. Para audio se ignora (ver download.py).
    clip_precise: bool = True


class ChannelReq(BaseModel):
    url: str
    quality: str = "best"
    interval_minutes: int = 60


class Job(BaseModel):
    id: str
    status: str = "queued"
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    note: str = ""
    filename: str = ""
    error: str = ""
    filepath: str = ""
    mime: str = ""
    created: float = 0.0
    finished: float = 0.0
    workdir: str = ""
    downloaded: int = 0
    total: int = 0
    title: str = ""
    incognito: bool = False
    clip_start: float | None = None
    clip_end: float | None = None
    clip_precise: bool = True


class BatchReq(BaseModel):
    urls: list[str]
    quality: str = "best"
    playlist_title: str = ""
    save_subfolder: bool = False


class SettingDef(BaseModel):
    """Metadata completa de un setting para exponer en la API."""
    key: str
    type: str
    scope: str
    value: Any
    default: Any
    origin: str
    locked: bool
    restart_required: bool = False
    description: str = ""
    placeholder: str = ""
    options: list[dict[str, str]] | None = None
    tokens: list[str] | None = None
    validation: dict[str, Any] | None = None


class SettingUpdate(BaseModel):
    """Request para PATCH /api/settings: body dict {key: value}."""
    pass
